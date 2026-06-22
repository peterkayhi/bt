import yfinance as yf
import pandas as pd
import duckdb
from datetime import date
from pathlib import Path
import os
import logging
from typing import NamedTuple, List

class btcacheResult(NamedTuple):
    final_df: pd.DataFrame
    missed_tickers: List[str]
    needed_starts: List[pd.Timestamp]

class btcache:
    def __init__(self):
        # Ensure the cache directory exists
        db_path = os.path.expanduser("~/.cache/finance_data/bt_cache.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        # Connect to DuckDB (creates file if it doesn't exist)
        self.con = duckdb.connect(db_path)
        
        # Self-healing schema migration: drop table if it's the old version without 'open'
        try:
            self.con.execute("SELECT open FROM prices LIMIT 1")
        except Exception:
            # Table doesn't have 'open' or doesn't exist yet
            self.con.execute("DROP TABLE IF EXISTS prices")
            
        # Store individual price points to allow flexible subset retrieval and range slicing
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                date DATE,
                ticker VARCHAR,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE,
                PRIMARY KEY (date, ticker)
            )
        """)

        # Setup a dedicated logger for btcache to avoid configuration conflicts
        self.logger = logging.getLogger("btcache")
        if not self.logger.handlers:
            self.logger.setLevel(logging.INFO)
            self.logger.propagate = False  # Keep cache logs isolated from the root logger
            formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
            log_dir = Path.home() / "Downloads" / "logFiles"
            log_dir.mkdir(parents=True, exist_ok=True)
            for handler in [logging.FileHandler(log_dir / "btcache.app.log", mode='a', encoding='utf-8'), logging.StreamHandler()]:
                handler.setFormatter(formatter)
                self.logger.addHandler(handler)
        self.logger.info(f"btcache initialized with database at {db_path}")

    def _key(self, ticker_list, start_date, end_date):
        # 1. Standardize ticker_list to list and create sorted key string
        if isinstance(ticker_list, str):
            ticker_list = [ticker_list]
        tickers_key = ",".join(sorted(ticker_list))
        
        # 2. Standardize dates to ISO strings for consistency
        if isinstance(start_date, date):
            start_date = start_date.strftime("%Y-%m-%d")
        if isinstance(end_date, date):
            end_date = end_date.strftime("%Y-%m-%d")
            
        # Ensure chronological order to prevent SQL BETWEEN and date_range failures
        s, e = sorted([start_date, end_date])

        # Convert to Timestamps to utilize pandas business day offsets
        s_ts = pd.to_datetime(s)
        e_ts = pd.to_datetime(e)

        # If end_date is today or in the future, cap it at yesterday to ensure we only request completed days
        if e_ts.date() >= pd.Timestamp.now().date():
            e_ts = e_ts - pd.Timedelta(days=1)

        # Ensure both dates are business days 
        # start date rolls forward
        s = pd.offsets.BusinessDay().rollforward(s_ts).strftime("%Y-%m-%d")
        # end date rolls back 
        e = pd.offsets.BusinessDay().rollback(e_ts).strftime("%Y-%m-%d")
        return ticker_list, tickers_key, s, e
    
    def get(self, ticker_list, start_date, end_date, skip_cache = False):
        # Centralized input cleaning and standardization via _key
        ticker_list, _, start_date, end_date = self._key(ticker_list, start_date, end_date)
        self.logger.info(f"Requesting data for tickers: {ticker_list} from {start_date} to {end_date}")

        # 1. Identify which tickers need a cache update
        missed_tickers = []
        needed_starts = []
        requested_start_dt = pd.to_datetime(start_date)

        for ticker in ticker_list:
            # Check if we have data for this ticker covering the requested range
            res = self.con.execute("""
                SELECT min(date)::VARCHAR, max(date)::VARCHAR FROM prices WHERE ticker = ?
            """, [ticker]).fetchone()
            
            # Download if ticker is totally missing, or cached range is insufficient
            # or we're being forced to ignore (skip) cache
            if not res or res[0] is None or res[0] > start_date or res[1] < end_date or skip_cache:
                missed_tickers.append(ticker)
                
                # Determine the most efficient start date for this specific ticker
                if not res or res[0] is None or res[0] > start_date or skip_cache:
                    needed_starts.append(requested_start_dt)
                else:
                    # Per request: start one day after the latest day in cache
                    needed_starts.append(pd.to_datetime(res[1]) + pd.Timedelta(days=1))

        # 2. Fetch and merge missing data or if we're explicitly told to skip caching
        if missed_tickers:
            self.logger.info(f"Tickers missing or needing update: {missed_tickers} with needed starts: {needed_starts}")
            # Use the earliest start date required by any ticker in the missing batch
            download_start = min(needed_starts).strftime("%Y-%m-%d")
            today = date.today().strftime("%Y-%m-%d") # Yahoo Finance gets exclusive end date, so we can use today to get up to yesterday's data
            new_data = yf.download(missed_tickers, start=download_start, end=today, auto_adjust=True, progress=False)
            
            if not new_data.empty:
                # Handle single vs. multi-ticker downloads
                if isinstance(new_data.columns, pd.MultiIndex):
                    # MultiIndex columns (Attribute, Ticker) - stack ticker level to rows
                    df_long = new_data.stack(level=1).reset_index()
                else:
                    # Single ticker (e.g. yfinance returned Open, High, Low, Close, Volume with single index)
                    df_long = new_data.reset_index()
                    df_long['ticker'] = missed_tickers[0]
                
                # Normalize column names to lowercase
                df_long.columns = [c.lower() for c in df_long.columns]
                
                # Ensure the standard required columns are present (fill missing with None)
                required_cols = ['date', 'ticker', 'open', 'high', 'low', 'close', 'volume']
                for col in required_cols:
                    if col not in df_long.columns:
                        df_long[col] = None
                        
                df_long = df_long.dropna(subset=['close'])
                df_to_insert = df_long[required_cols]

                # Upsert into DuckDB
                self.con.execute("INSERT OR REPLACE INTO prices SELECT * FROM df_to_insert")
            else: # we didn't get any data from the yfinance call
                self.logger.warning(f"Empty df from yfinance from {download_start} to {today}. Pulling from available cache")
        else: 
            self.logger.info("Cache hit")

        # 3. Retrieve the final requested subset from the database
        placeholders = ', '.join(['?'] * len(ticker_list))
        query = f"""
            SELECT date, ticker, open, high, low, close, volume FROM prices 
            WHERE ticker IN ({placeholders}) AND date >= ? AND date <= ?
        """
        params = ticker_list + [start_date, end_date]
        results_df = self.con.execute(query, params).df().dropna(subset=['close'])

        if results_df.empty:
            # Create the full calendar range requested by the user
            all_dates = pd.date_range(start=start_date, end=end_date)
            # Return an empty frame with requested tickers and dates (filled with NaN)
            # Use MultiIndex columns to match standard output structure
            cols = pd.MultiIndex.from_product([ticker_list, ['open', 'high', 'low', 'close', 'volume']], names=['ticker', 'attribute'])
            final_df = pd.DataFrame(index=all_dates, columns=cols)
            return btcacheResult(final_df, missed_tickers, needed_starts)

        # Pivot the database results back to "Wide" format (Date index, Ticker columns with multiple values)
        final_df = results_df.pivot(index='date', columns='ticker', values=['open', 'high', 'low', 'close', 'volume'])
        final_df.index = pd.to_datetime(final_df.index)
        
        # Reorder level to be (ticker, attribute) instead of (attribute, ticker)
        final_df = final_df.reorder_levels([1, 0], axis=1).sort_index(axis=1)
        
        return btcacheResult(final_df, missed_tickers, needed_starts)
