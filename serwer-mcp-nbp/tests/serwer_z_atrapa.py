"""Uruchamia prawdziwy serwer stdio z atrapą NBP zamiast sieci (tylko dla testu e2e)."""

from __future__ import annotations

from atrapa_nbp import AtrapaNBP

from serwer_mcp_nbp.serwer import sprawdz_srodowisko, uruchom

if __name__ == "__main__":
    import os

    sprawdz_srodowisko(os.environ)
    uruchom(AtrapaNBP().transport())
