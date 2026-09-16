FROM ghcr.io/home-assistant/home-assistant:2026.9.2

COPY *.whl /tmp/pyads-wheel/
RUN python -m pip install --no-index --no-deps --force-reinstall /tmp/pyads-wheel/*.whl \
    && python -c 'import pyads; from importlib.metadata import version; assert version("pyads") == "3.6.0"; port = pyads.open_port(); assert port; pyads.close_port(); print("pyads 3.6.0: AdsLib loaded, ADS port opened and closed")' \
    && rm -rf /tmp/pyads-wheel
