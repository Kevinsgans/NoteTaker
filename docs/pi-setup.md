# Raspberry Pi deployment

Target: Pi 5 + Camera Module 3 + AI HAT+ 2 (Hailo-10H). The setup below is a deployment recipe, not evidence that this machine has exercised those devices.

## Prepare hardware and native packages

Use supported 64-bit Raspberry Pi OS, suitable cooling/power, a fixed overhead camera mount, and a contrasting matte surface. Follow the official [AI HAT documentation](https://www.raspberrypi.com/documentation/accessories/ai-hat-plus.html) and [AI software installation](https://www.raspberrypi.com/documentation/computers/ai.html) for the currently matched Hailo-10H kernel driver, firmware, HailoRT Python package, and Qwen2-VL-2B-Instruct HEF. Do not substitute Hailo-8 packages or an arbitrary Hugging Face checkpoint.

Install the OS camera bindings and validate the camera independently:

```bash
sudo apt update
sudo apt install python3-picamera2 python3-venv
rpicam-hello --timeout 5000
hailortcli fw-control identify
```

Place the project at `/opt/notetaker` and the supported compiled model at `/opt/notetaker/models/qwen2-vl-2b-instruct.hef`. Keep the model outside Git. Install uv, then build a virtual environment from the OS interpreter so Picamera2/libcamera and the native Hailo bindings are visible:

```bash
cd /opt/notetaker
uv venv --python /usr/bin/python3 --system-site-packages
uv sync --locked --no-dev
```

The lockfile pins application dependencies. The system-installed native stack is intentionally not claimed as pinned until a successful smoke test. If OS camera bindings reject the NumPy ABI, install the matched distro/library versions in a dedicated tested environment, update the application lock deliberately, and rerun all tests; do not silently mix an incompatible native stack.

## Configure and test before enabling startup

Create the service account and data directory:

```bash
sudo useradd --system --user-group --groups video,render --home-dir /var/lib/notetaker notetaker
sudo install -d -o notetaker -g notetaker -m 0700 /var/lib/notetaker
sudo install -m 0644 deploy/pi.toml /etc/notetaker.toml
sudo vim /etc/notetaker.toml
sudo -u notetaker .venv/bin/notetaker --config /etc/notetaker.toml setup
```

Ensure the service account can read the model and access the Hailo device according to the vendor's udev rules. Do not run the application as root to work around device permissions.

First set `provider = "disabled"`, start the service manually, and save/retrieve an actual page. Then set `provider = "hailo"` and run:

```bash
sudo -u notetaker .venv/bin/notetaker --config /etc/notetaker.toml smoke --image /var/lib/notetaker/pages/PAGE_ID/corrected.png --output /var/lib/notetaker/hardware-smoke.json 2>&1 | rg '\[notetaker\]' > notetaker-smoke.log
```

Replace `PAGE_ID` with a captured page ID. This initializes the compiled VLM, performs inference under the timeout, and records model/config hashes, package versions, firmware identification, and memory information. Preserve the report, OS release, and installed Hailo package versions as the hardware compatibility record. Repeat after any native dependency change. The hardware/model versions are **unverified until this succeeds**.

## Startup, display, and recovery

```bash
sudo install -m 0644 deploy/notetaker.service /etc/systemd/system/notetaker.service
sudo systemctl daemon-reload
sudo systemctl enable --now notetaker
journalctl -u notetaker -f -o cat | rg '\[notetaker\]' > notetaker-debug.log
```

Open `http://PI_ADDRESS:8000` on the laptop; on the Pi open `http://127.0.0.1:8000`. Sign in as `notetaker` with your setup password. Install the kiosk desktop file in the Pi desktop user's `~/.config/autostart/` after confirming Chromium's command name. The display uses the same responsive browser UI; no display-specific driver is assumed.

HTTP Basic does not encrypt traffic. The default LAN example assumes a trusted private network. For untrusted/shared networks use an HTTPS reverse proxy or keep the app on localhost and use an SSH tunnel; never forward port 8000 publicly. For a laptop tunnel:

```bash
ssh -L 8000:127.0.0.1:8000 PI_USER@PI_ADDRESS
```

Set `host = "127.0.0.1"` when using the tunnel. No internet is needed after native packages and model installation.

After fixing camera/device permissions, recover a stopped camera worker with `sudo systemctl restart notetaker`. A failed model can be retried from the page without deleting the capture. Inspect storage health and free space before retrying disk-full errors. For backup, stop the service and copy the entire data directory, including SQLite/WAL files and image directories; retain permissions because the directory contains private notes and an authentication digest.
