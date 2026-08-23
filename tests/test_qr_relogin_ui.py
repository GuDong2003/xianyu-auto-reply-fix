from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_account_row_exposes_targeted_qr_relogin() -> None:
    script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")

    assert "function showAccountQRRelogin(accountId, mode = 'lite')" in script
    assert 'data-action="qr-relogin"' in script
    assert "showAccountQRRelogin('${cookie.id}', 'lite')" in script


def test_qr_modal_names_the_account_being_recovered() -> None:
    script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    page = (ROOT / "static/index.html").read_text(encoding="utf-8")

    assert "let qrLoginTargetAccountId = '';" in script
    assert "const normalizedAccountId = String(accountId || '').trim();" in script
    assert "qrLoginTargetAccountId = normalizedAccountId;" in script
    assert 'id="qrLoginTargetHint"' in page
    assert "新增账号或恢复掉线账号" in page


def test_external_connector_hides_unscoped_qr_actions() -> None:
    script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    page = (ROOT / "static/index.html").read_text(encoding="utf-8")

    assert "applyFrontendConnectionMode(result.connection_mode);" in script
    assert "function canStartQrLogin(accountId = '')" in script
    assert page.count('data-requires-legacy-connection="true"') == 2
    assert 'id="connectorQrModeHint"' in page


def test_production_image_contains_relogin_frontend_assets() -> None:
    dockerfile = (ROOT / "deploy/Dockerfile.production").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "COPY --chown=xianyu:xianyu . /app" in dockerfile
    assert "static/index.html" not in dockerignore
    assert "static/js/app.js" not in dockerignore
