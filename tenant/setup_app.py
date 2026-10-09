# /// script
# requires-python = ">=3.11"
# dependencies = ["azure-identity>=1.17", "httpx>=0.27", "cryptography>=42"]
# ///
"""Steps 3 and 4: register the demo app, grant admin consent, enable the TAP policy.

Run once, signed in as the tenant's Global Administrator:

    uv run tenant/setup_app.py              # opens a browser to sign in
    uv run tenant/setup_app.py --device-code

Safe to re-run: it reuses the app, the local certificate and existing grants.
Writes tenant/.env and tenant/.secrets/northwind-ai-tech.pem (never commit either).
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.x509.oid import NameOID

from graph import ENV_PATH, GRAPH_APP_ID, HERE, SECRETS_DIR, Graph, GraphError, q

APP_NAME = "northwind-ai-tech"
CERT_PATH = SECRETS_DIR / f"{APP_NAME}.pem"

# Microsoft's first-party "Microsoft Graph Command Line Tools" public client, the
# same one Connect-MgGraph uses. Only used for this one-time delegated setup.
CLI_TOOLS_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
SETUP_SCOPES = tuple(
    f"https://graph.microsoft.com/{s}"
    for s in (
        "Application.ReadWrite.All",
        "AppRoleAssignment.ReadWrite.All",
        "Policy.ReadWrite.AuthenticationMethod",
        "Organization.Read.All",
    )
)

# Application permissions from Tech.MD §7, plus Sites.Read.All so files.search
# can resolve a team site by name.
APP_ROLES = [
    "User.ReadWrite.All",
    "User.RevokeSessions.All",
    "LicenseAssignment.ReadWrite.All",
    "Group.ReadWrite.All",
    "GroupMember.ReadWrite.All",
    "Organization.Read.All",
    "UserAuthenticationMethod.ReadWrite.All",
    "AuditLog.Read.All",
    "IdentityRiskyUser.Read.All",
    "MailboxSettings.ReadWrite",
    "Files.ReadWrite.All",
    "Sites.Read.All",
]


def ensure_certificate() -> x509.Certificate:
    if CERT_PATH.exists():
        pem = CERT_PATH.read_bytes()
        return x509.load_pem_x509_certificate(pem[pem.index(b"-----BEGIN CERTIFICATE-----"):])
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, APP_NAME)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    SECRETS_DIR.mkdir(mode=0o700, exist_ok=True)
    CERT_PATH.write_bytes(
        key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()) + cert.public_bytes(Encoding.PEM)
    )
    CERT_PATH.chmod(0o600)
    print(f"  created certificate {CERT_PATH.relative_to(HERE.parent)}")
    return cert


def ensure_app(g: Graph, cert: x509.Certificate, role_ids: dict[str, str]) -> dict:
    body = {
        "displayName": APP_NAME,
        "signInAudience": "AzureADMyOrg",
        "keyCredentials": [{
            "type": "AsymmetricX509Cert",
            "usage": "Verify",
            "displayName": f"CN={APP_NAME}",
            "key": base64.b64encode(cert.public_bytes(Encoding.DER)).decode(),
        }],
        "requiredResourceAccess": [{
            "resourceAppId": GRAPH_APP_ID,
            "resourceAccess": [{"id": role_ids[r], "type": "Role"} for r in APP_ROLES],
        }],
    }
    app = g.first("/applications", {"$filter": f"displayName eq '{q(APP_NAME)}'"})
    if app:
        g.patch(f"/applications/{app['id']}", body)
        print(f"  updated app registration {APP_NAME} ({app['appId']})")
        return app
    app = g.post("/applications", body)
    print(f"  created app registration {APP_NAME} ({app['appId']})")
    return app


def ensure_service_principal(g: Graph, app_id: str) -> dict:
    sp = g.first("/servicePrincipals", {"$filter": f"appId eq '{app_id}'"})
    if sp:
        return sp
    sp = g.post("/servicePrincipals", {"appId": app_id}, retry_404=5)
    print("  created service principal")
    return sp


def grant_admin_consent(g: Graph, sp_id: str, graph_sp_id: str, role_ids: dict[str, str]) -> None:
    granted = {a["appRoleId"] for a in g.list(f"/servicePrincipals/{sp_id}/appRoleAssignments", retry_404=5)}
    for role in APP_ROLES:
        if role_ids[role] in granted:
            continue
        g.post(
            f"/servicePrincipals/{graph_sp_id}/appRoleAssignedTo",
            {"principalId": sp_id, "resourceId": graph_sp_id, "appRoleId": role_ids[role]},
            retry_404=5,
        )
        print(f"  granted {role}")


def enable_tap_policy(g: Graph) -> None:
    # Tenant-wide for simplicity; this is a demo tenant. One TAP lasts 8 hours by
    # default and can be used more than once so a new hire can retry setup.
    g.patch(
        "/policies/authenticationMethodsPolicy/authenticationMethodConfigurations/TemporaryAccessPass",
        {
            "@odata.type": "#microsoft.graph.temporaryAccessPassAuthenticationMethodConfiguration",
            "state": "enabled",
            "defaultLifetimeInMinutes": 480,
            "minimumLifetimeInMinutes": 60,
            "maximumLifetimeInMinutes": 480,
            "defaultLength": 8,
            "isUsableOnce": False,
            "includeTargets": [{"targetType": "group", "id": "all_users", "isRegistrationRequired": False}],
        },
    )
    print("  Temporary Access Pass policy enabled for all users")


def verify_app_token(tenant_id: str, client_id: str) -> None:
    """Sign in as the app and check the token carries every role. New apps and
    grants take a minute or two to propagate, so this polls."""
    from azure.identity import CertificateCredential

    deadline = time.time() + 300
    missing: set[str] = set(APP_ROLES)
    while time.time() < deadline:
        cred = CertificateCredential(tenant_id, client_id, certificate_path=str(CERT_PATH))
        try:
            token = cred.get_token("https://graph.microsoft.com/.default").token
        except Exception as exc:  # AADSTS700016 etc. until the app propagates
            print(f"  waiting for the app to propagate ({str(exc).splitlines()[0][:80]}...)")
            time.sleep(15)
            continue
        claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        missing = set(APP_ROLES) - set(claims.get("roles", []))
        if not missing:
            try:
                Graph(cred).get("/organization")
            except GraphError as exc:
                if exc.status != 401:  # Authorization_IdentityNotFound until the SP reaches Graph
                    raise
                print("  token has all roles; waiting for Graph to recognise the new app...")
                time.sleep(15)
                continue
            print(f"  app-only token OK with all {len(APP_ROLES)} roles; /organization readable")
            return
        print(f"  waiting for {len(missing)} role(s) to show up in the token...")
        time.sleep(15)
    raise SystemExit(f"App token still missing roles after 5 minutes: {sorted(missing)}. Re-run later.")


def main() -> None:
    from azure.identity import DeviceCodeCredential, InteractiveBrowserCredential

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device-code", action="store_true", help="sign in with a device code instead of a browser")
    args = parser.parse_args()

    cred_cls = DeviceCodeCredential if args.device_code else InteractiveBrowserCredential
    admin = Graph(cred_cls(client_id=CLI_TOOLS_CLIENT_ID, tenant_id="organizations"), SETUP_SCOPES)

    print("Signing in as admin...")
    org = admin.get("/organization")["value"][0]
    tenant_id = org["id"]
    domain = next(d["name"] for d in org["verifiedDomains"] if d.get("isInitial"))
    print(f"  tenant {org['displayName']} · {domain} · {tenant_id}")

    print("Step 3: app registration and admin consent")
    graph_sp = admin.first("/servicePrincipals", {"$filter": f"appId eq '{GRAPH_APP_ID}'", "$select": "id,appRoles"})
    role_ids = {r["value"]: r["id"] for r in graph_sp["appRoles"] if "Application" in r["allowedMemberTypes"]}
    cert = ensure_certificate()
    app = ensure_app(admin, cert, role_ids)
    sp = ensure_service_principal(admin, app["appId"])
    grant_admin_consent(admin, sp["id"], graph_sp["id"], role_ids)

    ENV_PATH.write_text(
        "# Written by setup_app.py. Do not commit.\n"
        f"AZURE_TENANT_ID={tenant_id}\n"
        f"AZURE_CLIENT_ID={app['appId']}\n"
        f"AZURE_CLIENT_CERTIFICATE_PATH={CERT_PATH.relative_to(HERE)}\n"
        f"M365_DOMAIN={domain}\n"
    )
    ENV_PATH.chmod(0o600)
    print(f"  wrote {ENV_PATH.relative_to(HERE.parent)}")

    print("Step 4: Temporary Access Pass policy")
    try:
        enable_tap_policy(admin)
    except GraphError as exc:
        print(f"  ! could not enable TAP policy: {exc}")

    print("Verifying app-only access...")
    verify_app_token(tenant_id, app["appId"])
    print("\nDone. Next: uv run tenant/seed.py")


if __name__ == "__main__":
    main()
