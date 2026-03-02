"""
local-test.py — Test the pipeline on your local computer
=========================================================
Works on Windows, Mac, and Linux.
Run with: python3 local-test.py

IMPORTANT SECURITY NOTE:
  Fill in your credentials below to test locally.
  NEVER commit this file to GitHub after filling it in.
  The .gitignore file in the repo excludes it for you.
"""

import os
import sys
import subprocess

# ═══════════════════════════════════════════════════════
# FILL IN YOUR REAL VALUES HERE
# ═══════════════════════════════════════════════════════

os.environ["SOURCE_TENANT_URL"]    = "https://your-company-source.api.identitynow.com"
os.environ["SOURCE_CLIENT_ID"]     = "your-source-client-id-here"
os.environ["SOURCE_CLIENT_SECRET"] = "your-source-client-secret-here"

os.environ["DEV_TENANT_URL"]       = "https://your-company-dev.api.identitynow.com"
os.environ["DEV_CLIENT_ID"]        = "your-dev-client-id-here"
os.environ["DEV_CLIENT_SECRET"]    = "your-dev-client-secret-here"

os.environ["PROD_TENANT_URL"]      = "https://your-company-prod.api.identitynow.com"
os.environ["PROD_CLIENT_ID"]       = "your-prod-client-id-here"
os.environ["PROD_CLIENT_SECRET"]   = "your-prod-client-secret-here"

# Leave blank if you have not set up Slack yet
os.environ["SLACK_WEBHOOK_URL"]    = ""

os.environ["CONFIG_FILE"] = "config-export.json"
os.environ["BACKUP_DIR"]  = "backups"

# ═══════════════════════════════════════════════════════
# DO NOT EDIT BELOW THIS LINE
# ═══════════════════════════════════════════════════════

def check_placeholders():
    """Warn user if they forgot to fill in the values above."""
    placeholders = [v for v in [
        os.environ["SOURCE_CLIENT_ID"],
        os.environ["SOURCE_CLIENT_SECRET"],
        os.environ["DEV_CLIENT_ID"],
        os.environ["DEV_CLIENT_SECRET"],
    ] if "your-" in v or v == ""]

    if placeholders:
        print("\n" + "="*55)
        print("ERROR: You have not filled in your credentials above.")
        print("Open local-test.py and replace the placeholder values")
        print("with your real SailPoint tenant URLs and API credentials.")
        print("="*55 + "\n")
        sys.exit(1)

def run_command(cmd_args):
    """Run a pipeline command and return the exit code."""
    print(f"\nRunning: python3 pipeline.py {' '.join(cmd_args)}")
    print("-" * 45)
    result = subprocess.run(
        [sys.executable, "pipeline.py"] + cmd_args,
        env=os.environ
    )
    print("-" * 45)
    return result.returncode

def main():
    check_placeholders()

    print("\n" + "="*55)
    print("SailPoint Pipeline — Local Test Runner")
    print("="*55)
    print("\nWhich command do you want to test?")
    print("  1) export       — Pull config from source tenant")
    print("  2) validate     — Check the exported config file")
    print("  3) deploy-dev   — Deploy to dev tenant")
    print("  4) deploy-prod  — Deploy to prod tenant")
    print("  5) rollback dev — Restore dev from latest backup")
    print("  6) rollback prod— Restore prod from latest backup")
    print("  7) full run     — Run export + validate + deploy-dev (all at once)")
    print()

    choice = input("Enter number (1-7): ").strip()

    commands = {
        "1": ["export"],
        "2": ["validate"],
        "3": ["deploy-dev"],
        "4": ["deploy-prod"],
        "5": ["rollback", "dev"],
        "6": ["rollback", "prod"],
    }

    if choice == "7":
        # Run the full pre-prod sequence one step at a time
        for cmd in [["export"], ["validate"], ["deploy-dev"]]:
            code = run_command(cmd)
            if code != 0:
                print(f"\n❌ Step '{' '.join(cmd)}' FAILED. Stopping.")
                sys.exit(1)
        print("\n✅ Full run (export + validate + deploy-dev) succeeded!")
        sys.exit(0)

    elif choice in commands:
        code = run_command(commands[choice])
        cmd_str = ' '.join(commands[choice])
        print(f"\n{'✅' if code == 0 else '❌'} Command '{cmd_str}' {'succeeded' if code == 0 else 'FAILED'}")
        sys.exit(code)

    else:
        print("Invalid choice. Enter a number between 1 and 7.")
        sys.exit(1)

if __name__ == "__main__":
    main()
