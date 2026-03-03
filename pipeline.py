"""
SailPoint ISC CI/CD Pipeline
==============================

Usage:
  python3 pipeline.py export
  python3 pipeline.py validate
  python3 pipeline.py deploy-dev
  python3 pipeline.py deploy-prod
  python3 pipeline.py rollback dev
  python3 pipeline.py rollback prod

All configuration comes from environment variables (set by Jenkins).
Works on Windows
"""

import os
import sys
import json
import time
import datetime
import urllib.request
import urllib.parse
import urllib.error
import ssl
from validator import SailPointValidator

ssl_context = ssl._create_unverified_context()
# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────

def get_config():
    """
    Load all settings from environment variables.
    Jenkins injects these from its credentials store.
    If anything is missing we print a clear message and stop.
    """
    required = [
        "SOURCE_TENANT_URL", "SOURCE_CLIENT_ID", "SOURCE_CLIENT_SECRET",
        "DEV_TENANT_URL",    "DEV_CLIENT_ID",    "DEV_CLIENT_SECRET",
        "PROD_TENANT_URL",   "PROD_CLIENT_ID",   "PROD_CLIENT_SECRET",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"\n[ERROR] Missing environment variables: {', '.join(missing)}")
        print("        Set these in Jenkins Credentials and inject via withCredentials()")
        print("        For local testing, fill them in at the top of local-test.py")
        sys.exit(1)

    return {
        "source": {
            "url":    os.environ["SOURCE_TENANT_URL"].rstrip("/"),
            "id":     os.environ["SOURCE_CLIENT_ID"],
            "secret": os.environ["SOURCE_CLIENT_SECRET"],
        },
        "dev": {
            "url":    os.environ["DEV_TENANT_URL"].rstrip("/"),
            "id":     os.environ["DEV_CLIENT_ID"],
            "secret": os.environ["DEV_CLIENT_SECRET"],
        },
        "prod": {
            "url":    os.environ["PROD_TENANT_URL"].rstrip("/"),
            "id":     os.environ["PROD_CLIENT_ID"],
            "secret": os.environ["PROD_CLIENT_SECRET"],
        },
        # Edit this list to match the object types your team manages
        "object_types": [
            "TRANSFORM",
            "RULE",
            "TRIGGER_SUBSCRIPTION",
            "ACCESS_PROFILE",
            "ROLE",
            "CONNECTOR_RULE",
        ],
        "config_file": os.environ.get("CONFIG_FILE", "config-export.json"),
        "test_config_file": os.environ.get("TEST_CONFIG_FILE", "config-export-test.json"),
        "slack_url":   os.environ.get("SLACK_WEBHOOK_URL", ""),
        "backup_dir":  os.environ.get("BACKUP_DIR", "backups"),
    }


# ─────────────────────────────────────────────────────────────
# HTTP HELPERS
# ─────────────────────────────────────────────────────────────

def http_post(url, data, headers=None, skip_content_type=False):
    """Send a POST request. Returns (status_code, response_body_string)."""
    headers = dict(headers) if headers else {}
    headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

    if isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode("utf-8")
        if not skip_content_type:
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    else:
        body = data.encode("utf-8") if isinstance(data, str) else data
        if not skip_content_type:
            headers.setdefault("Content-Type", "application/json")

    # 🔥 ALTERNATIVE FIX: Explicitly prevent Content-Type
    if skip_content_type:
        # Some APIs want no Content-Type header at all
        # Setting to empty string prevents urllib from adding its own
        headers["Content-Type"] = ""
    
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    
    # Remove the empty Content-Type we added
    if skip_content_type and "Content-Type" in req.headers:
        if req.headers["Content-Type"] == "":
            del req.headers["Content-Type"]
    
    try:
        with urllib.request.urlopen(req, context=ssl_context) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")

def http_post_raw(url, body_bytes, headers=None):
    """
    Send a POST request with pre-encoded body bytes.
    Does NOT modify Content-Type or body encoding.
    Returns (status_code, response_body_string).
    """
    headers = dict(headers) if headers else {}
    headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, context=ssl_context) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")        


def http_get(url, headers=None):
    """Send a GET request. Returns (status_code, response_body_string)."""
    headers = dict(headers) if headers else {}
    headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, context=ssl_context) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")

# ─────────────────────────────────────────────────────────────
# AUTHENTICATION
# ─────────────────────────────────────────────────────────────

def get_token(tenant):
    """
    Get an OAuth access token from SailPoint.
    Sends Client ID + Secret, gets back a temporary token valid ~1 hour.
    """
    log("Getting access token...")
    url = tenant["url"] + "/oauth/token"
    status, body = http_post(url, {
        "grant_type":    "client_credentials",
        "client_id":     tenant["id"],
        "client_secret": tenant["secret"],
    })
    if status != 200:
        die(f"Authentication failed (HTTP {status}): {body}")

    token = json.loads(body).get("access_token")
    if not token:
        die("No access_token in response. Check your Client ID and Secret.")

    log("Token obtained.")
    return token


# ─────────────────────────────────────────────────────────────
# EXPORT
# ─────────────────────────────────────────────────────────────

def export_config(tenant, object_types, output_file):
    """
    Export configuration from a tenant and save to a JSON file.
    Submits the export job, polls until done, downloads the result.
    """
    log(f"=== EXPORT from {tenant['url']} ===")
    token = get_token(tenant)
    auth_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Submit export job
    url = tenant["url"] + "/beta/sp-config/export"  # ✅ UPDATED
    payload = json.dumps({
        "description":  f"CI/CD Export {timestamp()}",
        "includeTypes": object_types
    })
    status, body = http_post(url, payload, auth_headers)
    if status != 202:
        die(f"Export submission failed (HTTP {status}): {body}")

    job_id = json.loads(body)["jobId"]
    log(f"Export job submitted. Job ID: {job_id}")

    # Poll until complete
    poll_until_done(tenant["url"], token, "export", job_id)

    # Download result
    dl_url = tenant["url"] + f"/beta/sp-config/export/{job_id}/download"  # ✅ UPDATED
    status, config_json = http_get(dl_url, auth_headers)
    if status != 200:
        die(f"Export download failed (HTTP {status}): {config_json}")

    # Save to file — create parent dirs if needed
    parent = os.path.dirname(output_file)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(config_json)

    obj_count = len(json.loads(config_json).get("objects", []))
    log(f"Export complete. {obj_count} objects saved to: {output_file}")


# ─────────────────────────────────────────────────────────────
# VALIDATE
# ─────────────────────────────────────────────────────────────

def validate_config(config_file):
    """
    Validate configuration export file using modular validator.
    
    Checks:
        - File exists and is valid JSON
        - Has required 'objects' array
        - Each object has proper structure (self, object sections)
        - Each object has required fields (type, id, name)
    
    Args:
        config_file: Path to the configuration export file
    
    Returns:
        None (calls die() if validation fails)
    """
    log(f"=== VALIDATE {config_file} ===")
    
    # Initialize validator
    validator = SailPointValidator(config_file)
    
    # Run validation (minimal POC version - no levels parameter)
    validation_passed = validator.validate()
    
    # Exit if validation failed
    if not validation_passed:
        die("Validation failed. Fix errors listed above before deploying.")
    
    log("Validation passed.")


# ─────────────────────────────────────────────────────────────
# BACKUP
# ─────────────────────────────────────────────────────────────

def backup_tenant(tenant, object_types, backup_dir, label):
    """
    Export the current tenant state to a timestamped backup file.
    Called before every deployment. Returns the backup file path.
    """
    log(f"Backing up current state of {label} tenant...")
    os.makedirs(backup_dir, exist_ok=True)
    backup_file = os.path.join(backup_dir, f"backup-{label}-{timestamp()}.json")
    export_config(tenant, object_types, backup_file)
    log(f"Backup saved: {backup_file}")
    return backup_file


# ─────────────────────────────────────────────────────────────
# IMPORT
# ─────────────────────────────────────────────────────────────

def import_config(tenant, config_file, label):
    """
    Import a config JSON file into a tenant.
    Submits the import job, polls until done, checks for object errors.
    """
    log(f"=== IMPORT into {label} ({tenant['url']}) ===")
    token = get_token(tenant)
    
    auth_headers = {"Authorization": f"Bearer {token}"}
    
    # Read the config file
    config_json = open(config_file, encoding="utf-8").read()
    
    # 🔥 FIX: Use multipart/form-data instead of raw JSON
    import random
    import string
    
    boundary = '----WebKitFormBoundary' + ''.join(random.choices(string.ascii_letters + string.digits, k=16))
    
    # Build multipart body
    body_parts = []
    body_parts.append(f'--{boundary}')
    body_parts.append('Content-Disposition: form-data; name="data"; filename="config.json"')
    body_parts.append('Content-Type: application/json')
    body_parts.append('')
    body_parts.append(config_json)
    body_parts.append(f'--{boundary}--')
    body_parts.append('')
    
    multipart_body = '\r\n'.join(body_parts).encode('utf-8')
    
    # Set proper multipart headers
    multipart_headers = dict(auth_headers)
    multipart_headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
    
    url = tenant["url"] + "/beta/sp-config/import"
    status, body = http_post_raw(url, multipart_body, multipart_headers)
    if status != 202:
        die(f"Import submission failed (HTTP {status}): {body}")
    
    job_id = json.loads(body)["jobId"]
    log(f"Import job submitted. Job ID: {job_id}")
    
    poll_until_done(tenant["url"], token, "import", job_id)
    
    result_url = tenant["url"] + f"/beta/sp-config/import/{job_id}"
    status, result_body = http_get(result_url, auth_headers)
    if status != 200:
        die(f"Could not retrieve import result (HTTP {status}): {result_body}")
    
    result = json.loads(result_body)
    error_count = 0
    for item in result.get("results", []):
        if item.get("status") == "ERROR":
            log(f"  [ERROR] Object failed: {json.dumps(item)}")
            error_count += 1
    
    if error_count > 0:
        die(f"Import completed but {error_count} object(s) had errors. See details above.")
    
    log(f"Import into {label} succeeded.")


# ─────────────────────────────────────────────────────────────
# ROLLBACK
# ─────────────────────────────────────────────────────────────

def do_rollback(tenant, backup_file, label):
    """
    Restore a tenant from a backup file.
    Returns True if rollback succeeded, False if it failed.
    Does NOT call sys.exit — lets the caller decide what to do.
    """
    log(f"=== ROLLBACK {label} from: {backup_file} ===")

    if not os.path.exists(backup_file):
        log(f"[ERROR] Backup file not found: {backup_file}")
        log("        Manual restore required via the SailPoint ISC admin UI.")
        return False

    try:
        import_config(tenant, backup_file, f"{label}-ROLLBACK")
        log(f"Rollback of {label} complete.")
        return True
    except SystemExit:
        log(f"[ERROR] Rollback import also failed for {label}.")
        log("        Manual restore required via the SailPoint ISC admin UI.")
        return False


# ─────────────────────────────────────────────────────────────
# devploy dev test
# ─────────────────────────────────────────────────────────────
def deploy_dev_test():
    """
    Deploy test configuration to dev tenant.
    Uses test_config_file instead of full export - safe for POC demos.
    NO automatic rollback - this is for testing/learning.
    """
    cfg = get_config()
    test_file = cfg["test_config_file"]
    
    log(f"=== TEST DEPLOY to DEV ({cfg['dev']['url']}) ===")
    
    if not os.path.exists(test_file):
        die(f"Test config not found: {test_file}\n"
            f"        Create this file first with test objects only.")
    
    log(f"📦 Using TEST config: {test_file}")
    validate_config(test_file)
    
    try:
        with open(test_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        die(f"Failed to read test config: {e}")
    
    obj_count = len(data.get('objects', []))
    log(f"\n🧪 TEST MODE: Will import {obj_count} object(s)")
    log("Objects to import:")
    for obj in data.get('objects', []):
        obj_type = obj.get('self', {}).get('type', 'UNKNOWN')
        obj_name = obj.get('object', {}).get('name', 'UNKNOWN')
        log(f"  • {obj_type}: {obj_name}")
    
    log("\n🛡️ SAFETY:")
    log("  • Test objects only (won't modify existing config)")
    log("  • Backup created (manual rollback if needed)")
    log("  • Easy to delete from UI after demo")
    log("  • No automatic rollback (this is for learning)")
    
    log(f"\n🎯 Target: {cfg['dev']['url']}")
    
    print("\nProceed with test import? (type 'yes' to confirm): ", end='', flush=True)
    confirm = input().strip()
    
    if confirm.lower() != 'yes':
        log("Import cancelled by user")
        sys.exit(0)
    
    backup_file = backup_tenant(
        cfg["dev"], cfg["object_types"], cfg["backup_dir"], "dev-test"
    )
    log(f"✅ Backup saved: {backup_file}")
    
    log("\n🚀 Starting import...")
    import_config(cfg["dev"], test_file, "DEV-TEST")
    
    log("\n" + "="*55)
    log("✅ TEST IMPORT COMPLETED SUCCESSFULLY")
    log("="*55)
    log(f"\n📁 Backup file (for manual rollback if needed):")
    log(f"   {backup_file}")
    log("\n📋 Next steps:")
    log("  1. Log into Dev tenant UI")
    log("  2. Verify test objects imported correctly")
    log("  3. Complete your POC demo")
    log("  4. Delete test objects from UI when done:")
    log("     • Admin → Transforms (or appropriate section)")
    log("     • Search for 'POC_TEST'")
    log("     • Delete the test object(s)")
    log("\n💡 If something went wrong:")
    log("  • Manual rollback: python3 pipeline.py rollback dev")
    log("  • Or delete test objects from UI")
    log("="*55)

# ─────────────────────────────────────────────────────────────
# POLLING
# ─────────────────────────────────────────────────────────────

def poll_until_done(base_url, token, job_type, job_id, timeout_sec=300):
    """
    Check job status every 5 seconds until complete, failed, or timed out.
    """
    url = f"{base_url}/beta/sp-config/{job_type}/{job_id}"  # ✅ UPDATED
    headers = {"Authorization": f"Bearer {token}"}
    start = time.time()

    while True:
        elapsed = int(time.time() - start)
        if elapsed > timeout_sec:
            die(f"Timed out after {timeout_sec}s waiting for {job_type} job {job_id}.")

        status, body = http_get(url, headers)
        if status != 200:
            die(f"Status check failed (HTTP {status}): {body}")

        try:
            job_status = json.loads(body).get("status", "UNKNOWN")
        except json.JSONDecodeError:
            die(f"Unexpected non-JSON response checking job status: {body[:200]}")

        log(f"  Job status: {job_status}  ({elapsed}s elapsed)")

        if job_status in ("COMPLETE", "COMPLETED"):
            return

        if job_status in ("FAILED", "ERROR"):
            die(f"{job_type} job {job_id} FAILED. Check SailPoint admin console for details.")

        time.sleep(5)


# ─────────────────────────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────────────────────────

def notify(message, slack_url=""):
    """Send a Slack message if configured. Never fails the pipeline."""
    log(f"[NOTIFY] {message}")
    if not slack_url:
        return
    try:
        http_post(slack_url, json.dumps({"text": message}), {"Content-Type": "application/json"})
    except Exception as e:
        log(f"[WARN] Slack notification failed (non-fatal): {e}")


# ─────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────

def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def die(msg):
    print(f"\n{'='*55}", flush=True)
    print(f"[FAILED] {msg}", flush=True)
    print(f"{'='*55}\n", flush=True)
    sys.exit(1)

def timestamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 pipeline.py <command>")
        print("  export           Export config from source tenant")
        print("  validate         Validate the exported config file")
        print("  deploy-dev       Deploy to dev (auto-rollback on failure)")
        print("  deploy-dev-test  Deploy TEST config to dev (safe for POC)")
        print("  deploy-prod      Deploy to prod (auto-rollback on failure)")
        print("  rollback dev     Manually restore dev from latest backup")
        print("  rollback prod    Manually restore prod from latest backup")
        sys.exit(1)

    command = sys.argv[1].lower()
    cfg = get_config()

    log("=" * 55)
    log(f"SailPoint ISC CI/CD Pipeline  —  {command}")
    log("=" * 55)

    if command == "export":
        export_config(cfg["source"], cfg["object_types"], cfg["config_file"])

    elif command == "validate":
        validate_config(cfg["test_config_file"])

    elif command == "deploy-dev":
        backup_file = backup_tenant(
            cfg["dev"], cfg["object_types"], cfg["backup_dir"], "dev"
        )
        try:
            import_config(cfg["dev"], cfg["config_file"], "DEV")
            notify("✅ Dev deployment succeeded. Awaiting prod approval.", cfg["slack_url"])
        except SystemExit:
            log("Import failed — attempting automatic rollback of dev...")
            notify("❌ Dev deployment FAILED. Rolling back.", cfg["slack_url"])
            success = do_rollback(cfg["dev"], backup_file, "dev")
            if success:
                notify("🔄 Dev rollback complete.", cfg["slack_url"])
            sys.exit(1)

    elif command == "deploy-prod":
        backup_file = backup_tenant(
            cfg["prod"], cfg["object_types"], cfg["backup_dir"], "prod"
        )
        try:
            import_config(cfg["prod"], cfg["config_file"], "PROD")
            notify("✅ Production deployment succeeded!", cfg["slack_url"])
        except SystemExit:
            log("Import failed — attempting automatic rollback of prod...")
            notify("❌ Production deployment FAILED. Rolling back!", cfg["slack_url"])
            success = do_rollback(cfg["prod"], backup_file, "prod")
            if success:
                notify("🔄 Prod rollback complete.", cfg["slack_url"])
            else:
                notify("🚨 PROD ROLLBACK ALSO FAILED — manual restore required!", cfg["slack_url"])
            sys.exit(1)
    elif command == "deploy-dev-test":
        deploy_dev_test()
    elif command == "rollback":
        valid_envs = {"dev", "prod"}
        env_arg = (sys.argv[2].lower() if len(sys.argv) > 2 else "")
        if env_arg not in valid_envs:
            die(f"Specify which environment to roll back: rollback dev   or   rollback prod")

        backup_dir = cfg["backup_dir"]
        if not os.path.isdir(backup_dir):
            die(f"Backup directory not found: {backup_dir}. No backups exist yet.")

        files = sorted([
            f for f in os.listdir(backup_dir)
            if f.startswith(f"backup-{env_arg}-") and f.endswith(".json")
        ])
        if not files:
            die(f"No backup files found in '{backup_dir}' for: {env_arg}")

        latest = os.path.join(backup_dir, files[-1])
        log(f"Most recent backup: {latest}")
        success = do_rollback(cfg[env_arg], latest, env_arg)
        if success:
            notify(f"🔄 Manual rollback of {env_arg} complete.", cfg["slack_url"])
        else:
            sys.exit(1)

    else:
        die(f"Unknown command: '{command}'\n"
            "        Valid: export | validate | deploy-dev | deploy-prod | rollback dev | rollback prod")

    log("=" * 55)
    log("COMPLETE")
    log("=" * 55)


if __name__ == "__main__":
    main()
