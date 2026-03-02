"""
SailPoint ISC CI/CD Pipeline
==============================
Single Python file. No compilation. No frameworks. Just HTTP calls.

Usage:
  python3 pipeline.py export
  python3 pipeline.py validate
  python3 pipeline.py deploy-dev
  python3 pipeline.py deploy-prod
  python3 pipeline.py rollback dev
  python3 pipeline.py rollback prod

All configuration comes from environment variables (set by Jenkins).
Works on Windows, Mac, and Linux.
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
        "slack_url":   os.environ.get("SLACK_WEBHOOK_URL", ""),
        "backup_dir":  os.environ.get("BACKUP_DIR", "backups"),
    }


# ─────────────────────────────────────────────────────────────
# HTTP HELPERS
# ─────────────────────────────────────────────────────────────

def http_post(url, data, headers=None):
    """Send a POST request. Returns (status_code, response_body_string)."""
    # Copy headers so we never mutate the caller's dict
    headers = dict(headers) if headers else {}

    if isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    else:
        body = data.encode("utf-8") if isinstance(data, str) else data
        headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, context=ssl_context) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def http_get(url, headers=None):
    """Send a GET request. Returns (status_code, response_body_string)."""
    headers = dict(headers) if headers else {}
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
    url = tenant["url"] + "/beta/config-object/export"
    payload = json.dumps({
        "description":  f"CI/CD Export {timestamp()}",
        "includeTypes": object_types,
        "options":      {"exportIds": True}
    })
    status, body = http_post(url, payload, auth_headers)
    if status != 202:
        die(f"Export submission failed (HTTP {status}): {body}")

    job_id = json.loads(body)["jobId"]
    log(f"Export job submitted. Job ID: {job_id}")

    # Poll until complete
    poll_until_done(tenant["url"], token, "export", job_id)

    # Download result
    dl_url = tenant["url"] + f"/beta/config-object/export/{job_id}/download"
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
    Check the exported JSON file is valid before deploying anywhere.
    Stops the pipeline if anything looks wrong.
    """
    log(f"=== VALIDATE {config_file} ===")

    if not os.path.exists(config_file):
        die(f"Config file not found: {config_file}\n"
            "        Run 'python3 pipeline.py export' first.")

    raw = open(config_file, encoding="utf-8").read().strip()
    if not raw:
        die("Config file is empty.")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        die(f"Config file is not valid JSON: {e}")

    if "objects" not in data:
        die("Config file is missing 'objects' array. Is this a genuine SailPoint Config Hub export?")

    objects = data["objects"]
    if not isinstance(objects, list) or len(objects) == 0:
        die("Config file has an empty 'objects' array — nothing would be deployed.\n"
            "        Check that the object_types in pipeline.py exist in your source tenant.")

    errors = []
    for i, obj in enumerate(objects):
        if "type" not in obj:
            errors.append(f"Object [{i}] missing 'type' field")
        if "object" not in obj:
            errors.append(f"Object [{i}] missing 'object' payload")

    if errors:
        for err in errors:
            log(f"  [ERROR] {err}")
        die(f"Validation failed with {len(errors)} error(s).")

    log(f"Validation passed. {len(objects)} objects ready to deploy.")


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
    auth_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    config_json = open(config_file, encoding="utf-8").read()

    # The Config Hub import endpoint takes the raw exported JSON as the body.
    # defaultConflict=OVERWRITE replaces existing objects with the same ID.
    url = tenant["url"] + "/beta/config-object/import?defaultConflict=OVERWRITE"
    status, body = http_post(url, config_json, auth_headers)
    if status != 202:
        die(f"Import submission failed (HTTP {status}): {body}")

    job_id = json.loads(body)["jobId"]
    log(f"Import job submitted. Job ID: {job_id}")

    # Poll until complete
    poll_until_done(tenant["url"], token, "import", job_id)

    # Check for object-level errors
    result_url = tenant["url"] + f"/beta/config-object/import/{job_id}"
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
# POLLING
# ─────────────────────────────────────────────────────────────

def poll_until_done(base_url, token, job_type, job_id, timeout_sec=300):
    """
    Check job status every 5 seconds until complete, failed, or timed out.
    """
    url = f"{base_url}/beta/config-object/{job_type}/{job_id}"
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
        validate_config(cfg["config_file"])

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
