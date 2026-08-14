"""Process bootstrap for the API and native voice dependencies."""

import os
import threading

from app.core.config import settings


def _calling_is_configured() -> bool:
    access_token = (
        settings.WHATSAPP_CALLING_ACCESS_TOKEN or settings.WHATSAPP_ACCESS_TOKEN
    )
    phone_number_id = (
        settings.WHATSAPP_CALLING_PHONE_NUMBER_ID or settings.WHATSAPP_PHONE_NUMBER_ID
    )
    return bool(
        settings.WHATSAPP_CALLING_ENABLED
        and access_token
        and phone_number_id
        and settings.SARVAM_API_KEY
    )


def main() -> None:
    # ONNX, Torch and WebRTC libraries can deadlock when their first import is
    # performed from an already-running asyncio lifecycle. Load them before
    # Uvicorn creates its event loop; FastAPI startup then sees a warm,
    # idempotently preloaded voice module.
    if _calling_is_configured():
        from app.services.voice_pipeline import preload_voice_pipeline_dependencies

        # A native BLAS/ONNX import has occasionally waited forever on this
        # host. Never leave Docker with a live-but-unhealthy process: the
        # Compose restart policy can recover only after the process exits.
        def abort_stuck_preload() -> None:
            print(
                "Voice dependency preload exceeded 30 seconds; restarting process",
                flush=True,
            )
            os._exit(70)

        preload_watchdog = threading.Timer(30.0, abort_stuck_preload)
        preload_watchdog.daemon = True
        preload_watchdog.start()
        try:
            preload_voice_pipeline_dependencies()
        finally:
            preload_watchdog.cancel()

    # Importing Uvicorn also imports its optional uvloop/native stack. Keep it
    # after Pipecat/ONNX so the two native stacks never initialize concurrently
    # or in the deadlocking order observed on this host.
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
