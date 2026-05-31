from typing import List

import pyads

from config import PLCConfig, Settings


def ensure_routes(settings: Settings) -> None:
    pyads.open_port()
    try:
        pyads.set_local_address(settings.sender_ams)
        for plc in settings.plcs:
            try:
                pyads.add_route_to_plc(
                    settings.sender_ams,
                    plc.host,
                    plc.ip,
                    settings.plc_username,
                    settings.plc_password,
                    route_name=settings.route_name,
                )
                print(f"Route added to {plc.name} ({plc.ip})")
            except Exception as exc:
                # Route may already exist or PLC unreachable; report and continue
                print(f"Route add warning for {plc.name} ({plc.ip}): {exc}")
        print(f"Local AMS: {pyads.get_local_address()}")
    finally:
        pyads.close_port()
