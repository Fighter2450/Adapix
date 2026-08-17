import sys, os, json
sys.path.insert(0, 'src')
os.environ.setdefault("ADAPIX_COOKIE_INSECURE", "1")
os.environ.setdefault("AUTO_PROVISION_NUMBERS", "false")
from fastapi.testclient import TestClient
from adapix.api.main import app

c = TestClient(app)
r = c.post("/auth/login", data={"email": "demo@adapix.test", "password": "demo12345"})
print("login:", r.status_code)
r = c.get("/api/v1/brief")
print("brief:", r.status_code)
print(json.dumps(r.json(), indent=2))
r2 = c.get("/api/v1/learning")
j2 = r2.json()
print("learning:", r2.status_code, "enough_data:", j2.get("enough_data"), "sends:", j2.get("sends"), "replies:", j2.get("replies"))
