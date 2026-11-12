import time
from fastapi.testclient import TestClient
from dashboard.app import app
from data.database import DatabaseManager

def test_guide_endpoints():
    db = DatabaseManager()
    if not db.get_user_by_username("Admin"):
        db.create_user("Admin", "Admin", must_change_password=True)
    else:
        db.reset_user_password("Admin", "Admin", force_first_login=True)

    client = TestClient(app)
    unique_user = f"guide_user_{int(time.time())}"
    res_first = client.post("/api/auth/first-login", json={
        "current_username": "Admin",
        "current_password": "Admin",
        "new_username": unique_user,
        "new_password": "MinhaSenhaForte@2026"
    })
    assert res_first.status_code == 200

    res = client.get("/api/guide/glossary")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert len(data["glossary"]) >= 5
    assert any(item["id"] == "trailing-stop" for item in data["glossary"])

    res_chat = client.post("/api/guide/chat", json={"message": "Como funciona o Trailing Stop?"})
    assert res_chat.status_code == 200
    data_chat = res_chat.json()
    assert data_chat["success"] is True
    assert "Trailing Stop" in data_chat["answer"]
    assert len(data_chat["suggestions"]) > 0

    res_cofre = client.post("/api/guide/chat", json={"message": "O que é o Cofre Reserva?"})
    assert res_cofre.status_code == 200
    data_cofre = res_cofre.json()
    assert "Cofre" in data_cofre["answer"]

    res_err = client.post("/api/guide/chat", json={"message": "  "})
    assert res_err.status_code == 400
