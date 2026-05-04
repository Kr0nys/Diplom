import io
import os
import time
import zipfile

import requests


BASE = os.environ.get("BASE_URL", "http://localhost:8000")
USER = os.environ.get("SMOKE_USER", "demo")
PASS = os.environ.get("SMOKE_PASS", "demo12345")
SAMPLE_DIR = os.environ.get("SAMPLE_DIR", os.path.join("samples", "simple_project"))


def make_zip_bytes(folder: str) -> bytes:
    folder = os.path.abspath(folder)
    skip_dirs = {".git", ".idea", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(folder):
            parts = set(os.path.normpath(root).split(os.sep))
            if parts & skip_dirs:
                continue
            for fn in files:
                if fn.endswith((".pyc", ".pyo")):
                    continue
                fp = os.path.join(root, fn)
                rel = os.path.relpath(fp, folder).replace("\\", "/")
                z.write(fp, arcname=rel)
    return buf.getvalue()


def main():
    token = requests.post(f"{BASE}/api/auth/login/", json={"username": USER, "password": PASS}).json()["access"]
    headers = {"Authorization": f"Bearer {token}"}

    session = requests.post(
        f"{BASE}/api/sessions/",
        headers=headers,
        json={
            "name": "smoke simple_project",
            "python_version": "3.9",
            "dependencies": [],
            "run_command": "python -c \"from mypkg.core import add; print(add(2,3))\"",
        },
    ).json()
    sid = session["id"]
    print("SESSION", sid)

    zbytes = make_zip_bytes(SAMPLE_DIR)
    files = {"archive": ("simple_project.zip", io.BytesIO(zbytes), "application/zip")}
    r = requests.post(f"{BASE}/api/sessions/{sid}/upload_files/", headers=headers, files=files)
    print("UPLOAD", r.status_code, r.text[:200])

    # wait analyzed
    for _ in range(120):
        s = requests.get(f"{BASE}/api/sessions/{sid}/", headers=headers).json()
        if s.get("status") in ("ANALYZED", "FAILED"):
            print("STATUS", s.get("status"))
            break
        time.sleep(1)

    # start generation
    gen_resp = requests.post(
        f"{BASE}/api/sessions/{sid}/generate_tests/",
        headers=headers,
        json={"detail_level": "basic", "use_mocks": False, "include_edge_cases": True, "test_framework": "pytest"},
    )
    gen = gen_resp.json()
    if gen_resp.status_code != 202 or "task_id" not in gen:
        raise RuntimeError(f"generate_tests failed: {gen_resp.status_code} {gen}")
    tid = gen["task_id"]
    print("GEN_TASK", tid)

    for _ in range(120):
        t = requests.get(f"{BASE}/api/test-tasks/{tid}/", headers=headers).json()
        if t.get("status") in ("COMPLETED", "FAILED"):
            print("TASK_STATUS", t.get("status"))
            print("ERROR", t.get("error_message"))
            tests = t.get("generated_tests") or ""
            print("TESTS_LEN", len(tests))
            print(tests[:400])
            return
        time.sleep(1)

    raise TimeoutError("Timeout waiting for test generation")


if __name__ == "__main__":
    main()

