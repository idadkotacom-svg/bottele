"""
Credential setup for Render deployment.
Restores credential files from base64-encoded environment variables.
Run locally with --encode to generate env var values from local files.
"""
import base64
import json
import os
import sys
import sys
from pathlib import Path
from dotenv import load_dotenv

CREDENTIALS_DIR = Path(__file__).parent / "credentials"
CREDENTIALS_DIR.mkdir(exist_ok=True)

# Mapping: env var name -> file path
CREDENTIAL_FILES = {
    "GOOGLE_SA_JSON_B64": CREDENTIALS_DIR / "service_account.json",
    "GOOGLE_CLIENT_SECRETS_B64": CREDENTIALS_DIR / "client_secrets.json",
    "GOOGLE_DRIVE_TOKEN_B64": CREDENTIALS_DIR / "drive_token.json",
}

# YouTube channel tokens: YOUTUBE_TOKEN_<NAME>_B64 -> youtube_token_<name>.json
def _get_channel_token_vars():
    """Discover YouTube token env vars dynamically."""
    extras = {}
    for key in os.environ:
        if key.startswith("YOUTUBE_TOKEN_") and key.endswith("_B64"):
            # YOUTUBE_TOKEN_DEFAULT_B64 -> youtube_token_default.json
            name = key[len("YOUTUBE_TOKEN_"):-len("_B64")].lower()
            extras[key] = CREDENTIALS_DIR / f"youtube_token_{name}.json"
    return extras


def restore_credentials():
    """Restore credential files from environment variables or secret files."""
    # Render mounts secret files at /etc/secrets/ or app root
    for secret_path in ["/etc/secrets/render_env_vars.txt", "render_env_vars.txt"]:
        if os.path.exists(secret_path):
            load_dotenv(secret_path)
            print(f"Loaded secrets from {secret_path}")

    all_creds = {**CREDENTIAL_FILES, **_get_channel_token_vars()}
    restored = 0

    for env_var, file_path in all_creds.items():
        b64_value = os.environ.get(env_var, "")
        if b64_value:
            try:
                decoded = base64.b64decode(b64_value)
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(decoded)
                print(f"✅ Restored: {file_path.name}")
                restored += 1
            except Exception as e:
                print(f"❌ Failed to restore {file_path.name}: {e}")
        else:
            if file_path.exists():
                print(f"⏭️ Already exists: {file_path.name}")
            else:
                print(f"⚠️ Missing env var: {env_var}")

    print(f"\n📁 {restored} credential files restored.")
    return restored


def encode_credentials():
    """Encode local credential files to base64 for Render env vars."""
    all_files = {**CREDENTIAL_FILES}

    # Also find existing YouTube tokens
    for token_file in CREDENTIALS_DIR.glob("youtube_token_*.json"):
        name = token_file.stem.replace("youtube_token_", "").upper()
        env_var = f"YOUTUBE_TOKEN_{name}_B64"
        all_files[env_var] = token_file

    print("=" * 60)
    print("  Copy these values to Render Environment Variables")
    print("=" * 60)
    print()

    for env_var, file_path in all_files.items():
        if file_path.exists():
            b64 = base64.b64encode(file_path.read_bytes()).decode()
            print(f"📋 {env_var}=")
            print(f"   {b64[:80]}...")
            print()
            
            # Also save to a file for easy copy
            output_file = Path(__file__).parent / "render_env_vars.txt"
            with open(output_file, "a") as f:
                f.write(f"{env_var}={b64}\n")
        else:
            print(f"⚠️ File not found: {file_path}")
    
    output_file = Path(__file__).parent / "render_env_vars.txt"
    if output_file.exists():
        print(f"\n✅ All values saved to: {output_file}")
        print("   Copy each line as an env var in Render Dashboard, or upload render_env_vars.txt as a Secret File mounted to /etc/secrets/render_env_vars.txt")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--encode":
        encode_credentials()
    else:
        restore_credentials()
