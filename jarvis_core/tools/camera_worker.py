"""Nimmt EIN Foto mit der Kamera auf (eigener Prozess, damit ein hängender Kameratreiber Jarvis nicht blockiert).

Aufruf: python -I camera_worker.py <ziel.jpg> [kamera-index]
Ausgabe: eine JSON-Zeile {"ok": true, "width": …, "height": …} oder {"ok": false, "error": "…"}.
Die Kamera ist nur für die Dauer der Aufnahme (rund 1–2 s) an; macOS zeigt dabei das grüne Licht."""
import json
import sys
import time


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "Zielpfad fehlt"}))
        return 2
    target, index = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 0
    try:
        import cv2
    except ImportError:
        print(json.dumps({"ok": False, "error": "opencv fehlt (pip install -r requirements-tools.txt)"}))
        return 2
    cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION) if sys.platform == "darwin" else cv2.VideoCapture(index)
    try:
        if not cap.isOpened():
            print(json.dumps({"ok": False, "error": "no_access"}))
            return 3
        frame, deadline = None, time.monotonic() + 2.5
        # Die ersten Bilder sind oft zu dunkel, bis die Belichtung nachgeregelt hat.
        for i in range(40):
            ok, img = cap.read()
            if ok and img is not None:
                frame = img
                if i >= 12 and img.mean() > 25:
                    break
            if time.monotonic() > deadline:
                break
        if frame is None:
            print(json.dumps({"ok": False, "error": "no_frame"}))
            return 4
        if not cv2.imwrite(target, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88]):
            print(json.dumps({"ok": False, "error": "Foto nicht speicherbar"}))
            return 5
        h, w = frame.shape[:2]
        print(json.dumps({"ok": True, "width": int(w), "height": int(h), "brightness": round(float(frame.mean()), 1)}))
        return 0
    finally:
        cap.release()


if __name__ == "__main__":
    sys.exit(main())
