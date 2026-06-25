"""
Main execution script for comparative ETF rotation backtests.
This script orchestrates data loading, sequential backtest execution for multiple
portfolios, and combined performance plotting and reporting.
"""
import os
import datetime
import backtrader as bt
import matplotlib.pyplot as plt
import pandas as pd
from papa_bear import PapaBearStrategy

# Configure matplotlib to use a professional visual style for generated charts
plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')

# List of portfolios to backtest, each with its custom name and list of tickers
import yaml

# Path to the portfolios YAML configuration file
PORTFOLIOS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolios.yaml")

try:
    with open(PORTFOLIOS_FILE, "r") as f:
        _config = yaml.safe_load(f)
    PORTFOLIOS = _config.get("portfolios", [])
except Exception as e:
    print(f"Error loading portfolios from {PORTFOLIOS_FILE}: {e}")
    raise SystemExit(f"Could not load portfolios: {e}")    

# Absolute path to the directory containing historical CSV data
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Backtest configuration constants
EVAL_START = datetime.date(2016, 1, 1)  # code will start pulling data 1 yr prior to calc momentums
TO_DATE = datetime.date(2026, 6, 1)  # or datetime.datetime.now()
START_CASH = 100000.0   # starting cash balance
COMMISSION = 0.000 # transaction fees, if any
SKIP_CACHE = False # set True to get data direct from yfinance
DOWNLOAD_DIR = "~/Downloads/backtrader" # directory holding generated data files
CHART_PREFIX = "bt_chart_" # prefix for chart filenames
TITLE_PREFIX = "Comparative Portfolio Performance ({} - {})" # brackets hold dates
COPYPASTE_FILE = "bt_copyPaste_" # prefix for copy paste filenames
DATA_FILE = "bt_alldata_" # prefix for data filenames

class CustomCSVData(bt.feeds.GenericCSVData):
    """
    Custom CSV Parser to correctly map yfinance's CSV output:
    Date,Close,High,Low,Open,Volume
    """
    # Define the mapping of CSV columns to Backtrader's internal data lines
    params = (
        ('nullvalue', 0.0),
        ('dtformat', '%Y-%m-%d'),
        ('datetime', 0),
        ('close', 1),
        ('high', 2),
        ('low', 3),
        ('open', 4),
        ('volume', 5),
        ('openinterest', -1),
    )

class CustomPandasData(bt.feeds.PandasData):
    """
    Custom Pandas Data feed mapping our cache MultiIndex DataFrame columns to Backtrader.
    """
    params = (
        ('datetime', None),
        ('open', 'open'),
        ('high', 'high'),
        ('low', 'low'),
        ('close', 'close'),
        ('volume', 'volume'),
        ('openinterest', -1),
    )

class CustomPapaBearStrategy(PapaBearStrategy):
    """
    Subclass PapaBearStrategy to track portfolio values and dates 
    specifically for the backtest window (starting EVAL_START)
    to enable custom comparative plotting.
    """
    def __init__(self):
        # Initialize parent strategy logic
        super().__init__()
        # Containers to store time-series data for the equity curve
        self.dates = []
        self.portfolio_values = []

    def next(self):
        # Retrieve the date of the current bar
        current_date = self.datas[0].datetime.date(0)
        # Start recording equity data once we're in the evaluation period
        if current_date >= EVAL_START:
            self.dates.append(current_date)
            self.portfolio_values.append(self.broker.getvalue())
            # Continue with standard strategy logic
            super().next()

def run_backtest():
    """
    Configures the Cerebro engine, loads ETF data, executes sequential backtests for
    all portfolios, and outputs unified plots, comparative CSVs, and statistics.
    """
    # 1. Load data feeds (starting from one year before EVAL_START to build the 12-month lookback history)
    from_date = EVAL_START - datetime.timedelta(days=365)
    to_date = TO_DATE  
    
    # Collect union of all unique tickers across all portfolios
    all_unique_tickers = sorted(list(set(ticker for p in PORTFOLIOS for ticker in p["tickers"])))
    
    print(f"Loading unified historical data feeds for tickers: {all_unique_tickers} using btcache...")
    from btcache import btcache
    cache = btcache()
    # Call the caching system once to fetch all ETFs at once
    cache_result = cache.get(all_unique_tickers, from_date, to_date, skip_cache=SKIP_CACHE)
    all_data = cache_result.final_df
    
    portfolio_results = {}
    portfolio_metrics = {}
    
    for portfolio in PORTFOLIOS:
        name = portfolio["name"]
        tickers = portfolio["tickers"]
        print(f"\n=================== RUNNING PORTFOLIO: {name} ===================")
        
        cerebro = bt.Cerebro()
        
        # Add data feeds for this portfolio's specific tickers
        for ticker in tickers:
            ticker_df = all_data[ticker]
            data = CustomPandasData(dataname=ticker_df, fromdate=from_date, todate=to_date)  # type: ignore
            cerebro.adddata(data, name=ticker)
            
        # Configure broker
        cerebro.broker.setcash(START_CASH)  
        cerebro.broker.setcommission(commission=COMMISSION)
        cerebro.broker.set_coc(True)
        cerebro.broker.set_checksubmit(False)
        
        # Add strategy & analyzers with custom portfolio_name parameter
        cerebro.addstrategy(CustomPapaBearStrategy, portfolio_name=name)
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe', riskfreerate=0.0, annualize=True)
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
        cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')
        
        print(f"Starting Portfolio Value: $   {START_CASH:,.2f}")  
        print(f"Running Backtest from {EVAL_START} to {TO_DATE}")
        
        # Run simulation
        results = cerebro.run()
        strat = results[0]
        
        # Extract performance metrics
        final_val = cerebro.broker.getvalue()
        sharpe_analysis = strat.analyzers.sharpe.get_analysis()
        drawdown_analysis = strat.analyzers.drawdown.get_analysis()
        
        sharpe = sharpe_analysis.get('sharperatio', 0.0)
        sharpe = 0.0 if sharpe is None else sharpe
        max_dd = drawdown_analysis.get('max', {}).get('drawdown', 0.0)
        
        # Calculate Compound Annual Growth Rate (CAGR) based on the actual duration of the test
        if len(strat.dates) > 0:
            start_date = strat.dates[0]
            end_date = strat.dates[-1]
            years = (end_date - start_date).days / 365.25
            cagr = ((final_val / START_CASH) ** (1.0 / years) - 1.0) * 100.0
        else:
            cagr = 0.0
            
        total_return = ((final_val - START_CASH) / START_CASH) * 100.0
        
        # Log finalized statistics to the console
        print(f"\n================ {name.upper()} RESULTS ================")
        print(f"Final Portfolio Value: ${final_val:,.2f}")
        print(f"Total Return:          {total_return / 100.0:.2%}")  
        print(f"CAGR:                  {cagr:.2f}%")
        print(f"Sharpe Ratio:          {sharpe:.2f}")
        print(f"Max Drawdown:          {max_dd:.2f}%")
        print("========================================================\n")
        
        # Store results and metrics
        portfolio_results[name] = {
            "dates": strat.dates,
            "values": strat.portfolio_values
        }
        
        portfolio_metrics[name] = {
            "Final Portfolio Value": f"${final_val:,.2f}",
            "Total Return": f"{total_return / 100.0:.2%}",
            "CAGR": f"{cagr:.2f}%",
            "Sharpe Ratio": f"{sharpe:.2f}",
            "Max Drawdown": f"{max_dd:.2f}%"
        }
        
    # Generate unified outputs
    artifact_dir = os.path.expanduser(DOWNLOAD_DIR)
    os.makedirs(artifact_dir, exist_ok=True)
    
    # 1. Output Combined Portfolio Details CSV (monthly resampled)
    save_combined_portfolio_details(portfolio_results, artifact_dir)
    
    # 2. Output Combined Portfolio Stats CSV (metrics as rows, portfolios as columns)
    save_portfolio_stats(portfolio_metrics, artifact_dir)
    
    # 3. Generate beautiful comparative line chart
    plot_combined_portfolios(portfolio_results, artifact_dir)

def save_combined_portfolio_details(portfolio_results, artifact_dir):
    """
    Saves portfolio values and corresponding dates for all portfolios to a single CSV file,
    resampled to Business Month End (BME).
    """
    series_dict = {}
    for name, data in portfolio_results.items():
        series = pd.Series(data["values"], index=pd.to_datetime(data["dates"]), name=name)
        series_dict[name] = series
        
    df = pd.DataFrame(series_dict)
    df.index.name = "Date"
    df.sort_index(inplace=True)
    df = df.resample("BME").last().dropna(how="all")
    df.reset_index(inplace=True)
    
    csv_path = os.path.join(artifact_dir, f"{COPYPASTE_FILE}{datetime.date.today().strftime('%Y-%m-%d')}.csv")
    df.to_csv(csv_path, index=False)
    print(f"Combined portfolio details saved to: {csv_path}")

def save_portfolio_stats(portfolio_metrics, artifact_dir):
    """
    Saves comparative portfolio statistics to a CSV file.
    Rows represent metrics, columns represent portfolios.
    """
    df = pd.DataFrame(portfolio_metrics)
    df.index.name = "Metric"
    df.reset_index(inplace=True)
    
    csv_path = os.path.join(artifact_dir, f"bt_stats_{datetime.date.today().strftime('%Y-%m-%d')}.csv")
    df.to_csv(csv_path, index=False)
    print(f"Comparative statistics saved to: {csv_path}")

def plot_combined_portfolios(portfolio_results, artifact_dir):
    """
    Generates a beautiful premium comparative line plot for all portfolios with a clean legend.
    """
    fig, ax = plt.subplots(figsize=(12, 6))
    
    # Premium curated color palette
    colors = ['#4F46E5', '#10B981', '#F59E0B', '#3B82F6', '#EF4444', '#EC4899', '#8B5CF6']
    
    for i, (name, data) in enumerate(portfolio_results.items()):
        color = colors[i % len(colors)]
        ax.plot(data["dates"], data["values"], color=color, linewidth=2.5, label=name)
        
    ax.set_title(TITLE_PREFIX.format(EVAL_START.year, TO_DATE.year), fontsize=14, fontweight='bold', pad=15)
    ax.set_xlabel("Date", fontsize=12, labelpad=10)
    ax.set_ylabel("Portfolio Value ($)", fontsize=12, labelpad=10)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, loc: f"{int(x):,}"))
    
    ax.legend(loc="upper left", frameon=True, facecolor="#F9FAFB", edgecolor="#E5E7EB")
    plt.tight_layout()
    
    plot_path = os.path.join(artifact_dir, f"{CHART_PREFIX}{datetime.date.today().strftime('%Y-%m-%d')}.png")
    plt.savefig(plot_path, dpi=300)
    print(f"Performance plot saved to: {plot_path}")
    plt.close()

if __name__ == "__main__":
    run_backtest()
