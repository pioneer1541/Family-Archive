"""NAS SMB mount service."""
import os
import subprocess
from typing import Optional

from app.logging_utils import get_logger, sanitize_log_context

logger = get_logger(__name__)

MOUNT_BASE = "/mnt/nas_shares"


def ensure_mount_point(path: str) -> bool:
    """Create mount point directory if not exists."""
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except Exception as e:
        logger.error(
            "failed_to_create_mount_point",
            extra=sanitize_log_context({"path": path, "error": str(e)}),
        )
        return False


def mount_smb_share(
    host: str, share_path: str, username: str = "", password: str = ""
) -> tuple[bool, str]:
    """
    Mount SMB share to local path.
    Returns (success, mount_point or error_message).
    """
    # Build mount point path
    safe_host = host.replace(".", "_")
    safe_share = share_path.strip("/").replace("/", "_")
    mount_point = f"{MOUNT_BASE}/{safe_host}/{safe_share}"

    # Ensure mount point exists
    if not ensure_mount_point(mount_point):
        return False, f"Failed to create mount point: {mount_point}"

    # If already mounted, unmount first
    if is_mounted(mount_point):
        umount_share(mount_point)

    # Build SMB path
    # share_path might be "volume1/Family_Archives" format
    smb_share = f"//{host}/{share_path.strip('/')}"

    # Build mount options (avoid password in command line)
    options = ["ro", "vers=3.0"]  # Read-only mount, SMB 3.0

    if username:
        options.append(f"username={username}")
        if password:
            # Write credentials to temporary file to avoid exposing in ps
            creds_file = f"/tmp/.smb_creds_{safe_host}_{safe_share}"
            try:
                with open(creds_file, "w") as f:
                    f.write(f"username={username}\n")
                    f.write(f"password={password}\n")
                os.chmod(creds_file, 0o600)
                options.append(f"credentials={creds_file}")
            except Exception as e:
                return False, f"Failed to create credentials file: {str(e)}"
    else:
        options.append("guest")

    cmd = [
        "sudo",
        "mount",
        "-t",
        "cifs",
        smb_share,
        mount_point,
        "-o",
        ",".join(options),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        # Clean up credentials file if created
        if username and password:
            try:
                if os.path.exists(creds_file):
                    os.remove(creds_file)
            except Exception:
                pass
        
        if result.returncode == 0:
            logger.info(
                "smb_mount_success",
                extra=sanitize_log_context(
                    {"share": smb_share, "mount_point": mount_point}
                ),
            )
            return True, mount_point
        else:
            logger.error(
                "smb_mount_failed",
                extra=sanitize_log_context(
                    {"share": smb_share, "error": result.stderr}
                ),
            )
            return False, f"Mount failed: {result.stderr}"
    except subprocess.TimeoutExpired:
        # Clean up credentials file on timeout
        if username and password:
            try:
                if os.path.exists(creds_file):
                    os.remove(creds_file)
            except Exception:
                pass
        return False, "Mount timeout"
    except Exception as e:
        # Clean up credentials file on error
        if username and password:
            try:
                if os.path.exists(creds_file):
                    os.remove(creds_file)
            except Exception:
                pass
        return False, f"Mount error: {str(e)}"


def umount_share(mount_point: str) -> bool:
    """Unmount a share."""
    try:
        subprocess.run(
            ["sudo", "umount", mount_point], capture_output=True, timeout=10
        )
        return True
    except Exception as e:
        logger.warning(
            "umount_failed",
            extra=sanitize_log_context(
                {"mount_point": mount_point, "error": str(e)}
            ),
        )
        return False


def is_mounted(mount_point: str) -> bool:
    """Check if path is currently mounted."""
    try:
        # Normalize mount point path
        normalized_mp = os.path.realpath(mount_point)
        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    # parts[1] is the mount point in /proc/mounts
                    mounted_at = os.path.realpath(parts[1])
                    if mounted_at == normalized_mp:
                        return True
        return False
    except Exception:
        return False


def get_mount_status() -> dict:
    """Get current NAS mount status."""
    import glob

    # Check default mount points
    mount_points = glob.glob(f"{MOUNT_BASE}/**/*", recursive=True)

    for mp in mount_points:
        if os.path.isdir(mp) and is_mounted(mp):
            # Get disk usage
            try:
                result = subprocess.run(
                    ["df", "-h", mp], capture_output=True, text=True
                )
                df_output = (
                    result.stdout.strip().split("\n")[-1] if result.stdout else ""
                )
                parts = df_output.split()
                if len(parts) >= 6:
                    return {
                        "mounted": True,
                        "mount_point": mp,
                        "source": parts[0] if parts else "unknown",
                        "size_info": {
                            "total": parts[1] if len(parts) > 1 else "unknown",
                            "used": parts[2] if len(parts) > 2 else "unknown",
                            "available": parts[3] if len(parts) > 3 else "unknown",
                            "use_percent": parts[4]
                            if len(parts) > 4
                            else "unknown",
                        },
                        "last_error": None,
                    }
            except Exception as e:
                return {
                    "mounted": True,
                    "mount_point": mp,
                    "last_error": str(e),
                }

    return {"mounted": False, "mount_point": None, "last_error": None}


def restart_worker_container() -> tuple[bool, str]:
    """Restart fkv-worker-dev container to apply new mounts."""
    try:
        # Use docker compose to restart worker
        compose_files = [
            "/app/docker-compose.dev.yml",
            "/app/docker-compose.yml",
            "docker-compose.dev.yml",
            "docker-compose.yml",
        ]

        for compose_file in compose_files:
            if os.path.exists(compose_file):
                result = subprocess.run(
                    [
                        "docker",
                        "compose",
                        "-f",
                        compose_file,
                        "restart",
                        "fkv-worker-dev",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode == 0:
                    return True, "Worker restarted successfully"
                else:
                    # Try with just 'fkv-worker' service name
                    result2 = subprocess.run(
                        [
                            "docker",
                            "compose",
                            "-f",
                            compose_file,
                            "restart",
                            "fkv-worker",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    if result2.returncode == 0:
                        return True, "Worker restarted successfully"
                    return False, f"Restart failed: {result.stderr}"

        return False, "No docker-compose file found"
    except Exception as e:
        return False, f"Restart error: {str(e)}"
