"""
This module provides utility functions to download historical ETF data from Yahoo Finance.
The downloaded data is stored as CSV files for use in Backtrader backtesting strategies.
"""
import os
import datetime
import yfinance as yf
import pandas as pd

# List of ETF tickers to include in the historical data universe
ETFS = ["BNDX", "DBC", "EEM", "EFA", "GLD", "IEF", "IWD", "IWF", "IWN", "IWO", "LQD", "TLT", "VNQ"]
# Absolute path to the directory where data files will be stored
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

def download_etf_data():
    """
    Downloads historical price data for the defined list of ETFS and saves them to local CSVs.
    Handles directory creation, data retrieval, and column formatting.
    """
    # Ensure the target directory for data storage exists
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # Set the starting point for the historical lookback
    start_date = "2014-01-01"
    # Use today's date as the end point for the dataset
    end_date = datetime.datetime.now().strftime("%Y-%m-%d")
    
    print(f"Downloading historical data from {start_date} to {end_date} using yfinance...")
    
    # Process each ticker in the universe individually
    for ticker in ETFS:
        print(f"Downloading {ticker}...")
        try:
            # Fetch historical OHLCV data from the yfinance API
            df = yf.download(ticker, start=start_date, end=end_date, progress=False)
            
            # Check if the API returned an empty result for the ticker
            if df.empty:
                print(f"No data returned for {ticker}")
                continue
                
            # Flatten MultiIndex columns if present (common in newer yfinance versions)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            # Standardize the index name to 'Date' for consistent CSV headers
            df.index.name = "Date"
            
            # Define the final save path for the CSV file
            filepath = os.path.join(DATA_DIR, f"{ticker}.csv")
            # Export the dataframe to a CSV file
            df.to_csv(filepath)
            print(f"Successfully saved {ticker}.csv ({len(df)} rows) to {filepath}")
        except Exception as e:
            # Catch and log any unexpected errors during download or file writing
            print(f"Error downloading {ticker}: {e}")

if __name__ == "__main__":
    # Execute the main download function
    download_etf_data()
