#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prepare_client.py - Automatically download, update, and run the Nova executor client
Just run: python prepare_client.py
"""

import os
import sys
import subprocess
import urllib.request
import hashlib

# Configuration
GITHUB_REPO = "https://raw.githubusercontent.com/bhoshaga/winstru-executor/main"
FILES_TO_CHECK = ["client.py", "requirements.txt"]

def get_file_hash(filepath):
    """Calculate SHA256 hash of a file"""
    if not os.path.exists(filepath):
        return None

    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def download_file(url, filepath):
    """Download a file from URL to filepath"""
    try:
        print(f"Downloading {filepath}...")
        with urllib.request.urlopen(url) as response:
            content = response.read()

        # Save to file
        with open(filepath, 'wb') as f:
            f.write(content)

        print(f"✓ Downloaded {filepath}")
        return True
    except Exception as e:
        print(f"✗ Failed to download {filepath}: {e}")
        return False

def get_python_executable():
    """Get the correct Python executable path"""
    # Check if we're in a virtual environment
    if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        # In a virtual environment
        if os.name == 'nt':  # Windows
            venv_python = os.path.join(sys.prefix, 'Scripts', 'python.exe')
        else:  # Unix-like
            venv_python = os.path.join(sys.prefix, 'bin', 'python')

        if os.path.exists(venv_python):
            return venv_python

    # Default to current Python
    return sys.executable

def get_pip_command():
    """Get the correct pip command"""
    python_exe = get_python_executable()
    return [python_exe, "-m", "pip"]

def main():
    print("=" * 60)
    print("Nova Executor Prepare Script")
    print("=" * 60)

    # Track if any files were updated
    files_updated = False
    requirements_updated = False

    # Check and download each file
    for filename in FILES_TO_CHECK:
        filepath = filename
        url = f"{GITHUB_REPO}/{filename}"

        # Get hash of existing file
        old_hash = get_file_hash(filepath)

        # Download to temp file first
        temp_filepath = f"{filepath}.tmp"
        if download_file(url, temp_filepath):
            # Get hash of new file
            new_hash = get_file_hash(temp_filepath)

            # Compare hashes
            if old_hash != new_hash:
                # Replace old file with new one
                if os.path.exists(filepath):
                    os.remove(filepath)
                os.rename(temp_filepath, filepath)
                print(f"✓ Updated {filename}")
                files_updated = True

                if filename == "requirements.txt":
                    requirements_updated = True
            else:
                # No changes, remove temp file
                os.remove(temp_filepath)
                print(f"✓ {filename} is up to date")
        else:
            # Download failed
            if not os.path.exists(filepath):
                print(f"✗ Error: {filename} not found and download failed")
                sys.exit(1)
            else:
                print(f"⚠ Using existing {filename}")

    # Install requirements if needed
    if requirements_updated or not os.path.exists("requirements.txt"):
        print("\n" + "=" * 60)
        print("Installing/Updating dependencies...")
        print("=" * 60)

        pip_cmd = get_pip_command()
        try:
            result = subprocess.run(
                pip_cmd + ["install", "-r", "requirements.txt"],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                print("✓ Dependencies installed successfully")
            else:
                print(f"⚠ Warning: Some dependencies may have failed to install")
                print(f"Error: {result.stderr}")
        except Exception as e:
            print(f"✗ Failed to install dependencies: {e}")
            print("You may need to run: pip install -r requirements.txt manually")

    # Done - user will run client.py manually
    print("\n" + "=" * 60)
    print("✓ Setup complete!")
    print("To start the Nova Executor Client, run:")
    print(f"  python client.py")
    print("=" * 60)

if __name__ == "__main__":
    main()