| # | Role | Part / SKU | Qty | Electrical | Notes | Source |
|---|---|---|---|---|---|---|
| 1 | Output — arm actuators | **Feetech STS3215** 12 V serial bus servos (on SO-ARM101) | 6 | 12 V nominal; **≈ 0.3 A idle / ≈ 1.0 A typical motion / stall ≈ 2.7 A each** | 6-DoF chain; IDs 1..6 shoulder → gripper; 1 Mbps half-duplex | [partabot.com/products/so-arm101-follower-only](https://partabot.com/products/so-arm101-follower-only) |
| 2 | Output — arm driver | **Waveshare Bus Servo Adapter (A)** | 1 | 3.3 V logic / 9–12.6 V bus pass-through | Accepts 3.3 V TTL from J32; bus is 12 V half-duplex | Waveshare |
| 3 | Output — arm power | **12 V / 3 A DC adapter** (barrel, 5.5×2.5 mm) | 1 | 12 V, 36 W max | SO-ARM101 kit bundle; see §3.3.3 headroom note | SO-ARM101 kit |
| 4 | Output — hand actuators | **Feetech SCS0009** 5 V serial bus servos (on AmazingHand) | 8 | 5 V bus (direct from 5 V kit adapter; no regulator in the path); **≈ 0.1 A idle / ≈ 0.3 A typical / stall ≈ 0.8 A each** | 4 fingers × 2 servos (opposed pair); 1 Mbps half-duplex | [seeedstudio.com/Amazing-Hand-Right-Hand](https://www.seeedstudio.com/Amazing-Hand-Right-Hand-The-Open-Source-Robotic-Hand-Developer-Kit.html) |
| 5 | Output — hand driver | **Seeed Studio Bus Servo Driver Board for XIAO** | 1 | 5 V input (kit-supplied); passes straight through to SCS0009 bus — **no on-board voltage regulation** | **Front jumper MUST be removed** to expose UART directly; input voltage = bus voltage (no regulator between plug and servos) | Seeed (AmazingHand kit) |
| 6 | Output — hand power | **5 V / 3 A DC adapter** (barrel, 5.5×2.5 mm) | 1 | 5 V, 15 W max; direct pass-through to SCS0009 bus via Seeed board | AmazingHand kit bundle | AmazingHand kit |
| 7 | USB↔TTL bridge — hand bus | **Waveshare CH343** USB-Enhanced-SERIAL (VID 1A86 / PID 55D3) | 1 | 3.3 V signal; bus-powered from host USB | Enumerates as `COM18` on this host; 1 Mbps half-duplex; signal only — does not power servos | Waveshare |
| 8 | Host development PC | **Dell Inspiron 16 Plus 7620** (i7-12700H, 40 GiB RAM, Intel Iris Xe iGPU) | 1 | Mains AC | Sole dev + runtime target — no embedded board, no cross-compile. See §1 for full specs. | Owner |
| 9 | Imaging — fundus camera | **Optomed Aurora** handheld fundus camera (TBD model/SKU) | 1 | TBD (own battery; Wi-Fi link to host) | Patient retinal imaging; controlled read-only over the Pictor Wi-Fi API (`fundus_config.yaml`). Hand presses its shutter in `grab_trigger_capture`. | TBD |
| 10 | Imaging — system camera | **IFWATER IF-USB12MP02AF-V65-A** PDAF USB camera module (Sony IMX362, 12 MP, 65° no-distortion lens) | 1 | 5 V via host USB; **USB 2.0** UVC | Arm-mounted; films the Aurora's screen for the live cv2 preview/record (`system_camera_config.yaml`); enumerates as camera index 1 on this host. Max still **4000×3000 (MJPG)**, USB 2.0. Live preview + recording + arc detection stream at **1600×1200 (UXGA, 4:3)** (`system_camera_config.yaml` `width`/`height`) for ~44 fps over USB 2.0 — the largest 4:3 MJPG mode before the bandwidth cliff to ~15 fps (enumerate with `usb_camera_modes.py`); the fixed ROI crops ~490×368 real px and upscales ~1.3× to the 640×480 reference (a deliberate fps trade; 2592×1944 is the no-upscale alternative at ~15 fps). `_apply_format` brackets the size with MJPG so the driver can't fall back to uncompressed YUY2 (which would drop high-res modes to ~2 fps). `usb_camera_capture` saves the current **1600×1200** stream frame on each SPACE grab (no full-res reopen). **Manual focus locked at `focus: 600` (autofocus off)** so the Aurora screen stays sharp (no AF hunting/breathing); this **requires `backend: dshow`** — only DSHOW drives this unit's VCM (MSMF ignores `CAP_PROP_FOCUS`). Value found with `usb_camera_focus_probe.py`. | IFWATER (Amazon) |

---

## §1 — Host PC details (probed 2026-05-04)

| Component | Spec |
|---|---|
| Model | Dell Inspiron 16 Plus 7620 |
| CPU | 12th Gen Intel Core i7-12700H — 14 cores (6P + 8E) / 20 threads, 2.3 GHz base |
| Cache | L2 11.5 MiB / L3 24 MiB |
| RAM | 39.7 GiB usable (42,619,097,088 bytes) |
| GPU | Intel Iris Xe Graphics (integrated, ~2 GiB shared); **no discrete GPU**. Two DisplayLink USB virtual adapters present (external monitors). |
| OS | Windows 11 Home, build 22635 (Insider — Beta channel), 64-bit |
| Storage | C: 656 GB / 250 GB free (NTFS); D: 295 GB / 170 GB free (NTFS); plus removable FAT32 volumes |
| USB stack | Intel USB 3.10 xHCI + Intel USB 3.20 xHCI + USB4 host router |
| Active COM ports | `COM18` = USB-Enhanced-SERIAL CH343 (AmazingHand bridge); COM3/4/10/11 = Bluetooth virtual ports (unused) |

**Implications for development choices:**
- **CPU/RAM headroom is large** — Python's interpreter overhead and GIL are not bottlenecks for any control loop the bus can sustain (~200 Hz max at 1 Mbps half-duplex with 5+8 servos).
- **No discrete GPU** — local ML training is CPU-bound (Intel iGPU has no usable PyTorch/CUDA path on Windows). Inference of small policies is fine on CPU; large-policy training requires cloud or a separate machine.
- **Windows 11 Home (no PREEMPT_RT)** — soft real-time only. Hard real-time guarantees are unattainable regardless of language; for sub-millisecond determinism we'd need a separate MCU.
- **USB4 + USB 3.20** — serial bus bandwidth is never the bottleneck; the 1 Mbps motor bus is the constraint.
- **Single CH343 bridge today** — adding the SO-ARM101 will enumerate a second COM port (likely a Waveshare bus adapter); discover with `Get-PnpDevice -Class Ports -Status OK`.