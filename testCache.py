""" 
hello world code showing btcache working
"""
import pandas as pd
from btcache import btcache

tickers_param: list[str] = [
    'VTV', 'VUG', 'VIOV', 'VIOG', 'VEA', 'VWO', 'VNQ',
    'PDBC', 'IAU', 'EDV', 'VGIT', 'VCLT', 'BNDX'
]
start_date = "2025-01-01"  #code will start pulling data 1 yr prior to calc momentums
end_date = "2025-12-31"  # test with just 1 yr of data
skip_cache = False

# =============================================================================
# Pull data from cache
# =============================================================================
# Fix for the bug you spotted: pull extra history so 12-month momentum works from day 1
download_start = (pd.to_datetime(start_date) - pd.DateOffset(months=15)).strftime("%Y-%m-%d")
cache = btcache() # get our Yahoo data via caching - reduces api call guilt
dataget = cache.get( tickers_param, download_start, end_date, skip_cache)
data = dataget.final_df
print(data.head(10))
print(data.tail(10))