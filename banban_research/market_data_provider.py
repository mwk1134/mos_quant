"""Import boundary for the original backtester; implicit live data is disabled."""


class MarketDataProvider:
    def __init__(self, *args, **kwargs):
        raise RuntimeError('Offline snapshot required; this share has no live market provider')
