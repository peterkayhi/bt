"""
Main execution script for the Papa Bear momentum-based ETF backtest.
This script orchestrates data loading, strategy execution, and performance visualization.
"""
import os
import datetime
import backtrader as bt
import matplotlib.pyplot as plt
from papa_bear import PapaBearStrategy

# Configure matplotlib to use a professional visual style for generated charts
plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')

# List of target ETF tickers for the strategy universe
ETFS = [
    'VTV', 'VUG', 'VIOV', 'VIOG', 'VEA', 'VWO', 'VNQ','PDBC', 'IAU', 'EDV', 'VGIT', 'VCLT', 'BNDX'
    ]
# Absolute path to the directory containing historical CSV data
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Backtest configuration constants
EVAL_START = datetime.date(2015, 1, 1)  #code will start pulling data 1 yr prior to calc momentums
TO_DATE = datetime.date(2026, 6, 1)  # or datetime.datetime.now()
START_CASH = 100000.0   # starting cash balance
COMMISSION = 0.000 # transaction fees, if any
SKIP_CACHE = False # set True to get data direct from yfinance
DOWNLOAD_DIR = "~/Downloads/backtrader" #directory holding generated data files
CHART_PREFIX = "bt_chart_" #prefix for chart filenames
TITLE_PREFIX = "Livingston's Papa Bear Portfolio Performance ({} - {})" #brackets hold dates
COPYPASTE_FILE = "bt_copyPaste_" #prefix for copy paste filenames
DATA_FILE = "bt_alldata_" #prefix for data filenames
PLOT_LABEL = "Papa Bear Portfolio Value" #label for the plot

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
    specifically for the backtest window (starting 2015-01-01)
    to enable custom premium plotting.
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
    Configures the Cerebro engine, loads ETF data, executes the backtest, and plots results.
    """
    cerebro = bt.Cerebro()
    
    # 1. Load data feeds (starting from one year before START_EVAL  to build the 12-month lookback history)
    from_date = EVAL_START - datetime.timedelta(days=365)
    to_date = TO_DATE  
    
    print("Loading ETF historical data feeds using btcache...")
    from btcache import btcache
    cache = btcache()
    # Call the caching system to fetch all ETFs at once
    cache_result = cache.get(ETFS, from_date, to_date, skip_cache=SKIP_CACHE)
    all_data = cache_result.final_df
    
    for ticker in ETFS:
        # Slice the MultiIndex DataFrame for the specific ticker
        # all_data columns are a MultiIndex with levels: (ticker, attribute)
        # e.g. all_data[ticker] has columns: ['open', 'high', 'low', 'close', 'volume']
        ticker_df = all_data[ticker]
        
        # Use our CustomPandasData parser to map the sliced dataframe
        data = CustomPandasData(dataname=ticker_df, fromdate=from_date, todate=to_date)  # type: ignore
        # Register the data feed with a specific ticker name for internal reference
        cerebro.adddata(data, name=ticker)


    # 2. Configure broker
    cerebro.broker.setcash(START_CASH)  
    # vanguard etfs have no commission.  caution: you may get errors based on the cash buffer
    cerebro.broker.setcommission(commission=COMMISSION)
    # Cheat on close to allow same-day rebalance order execution
    cerebro.broker.set_coc(True)
    # Disable submission cash checks to allow rebalancing sells/buys on same bar
    cerebro.broker.set_checksubmit(False)

    # 3. Add strategy & analyzers
    # Inject our wrapped strategy into the engine
    cerebro.addstrategy(CustomPapaBearStrategy)
    # Attach performance analyzers to measure risk and return metrics
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe', riskfreerate=0.0, annualize=True)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
    cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')

    print(f"\nStarting Portfolio Value: $   {START_CASH:,.2f}")  
    print(f"Running Backtest from {EVAL_START} to {TO_DATE}")

    
    # Run the simulation and capture the resulting strategy instance
    results = cerebro.run()
    strat = results[0]

    # Save all data from cerebro to alldata.csv (must be after run so line buffers are populated)
    artifact_dir = os.path.expanduser(DOWNLOAD_DIR)
    os.makedirs(artifact_dir, exist_ok=True)
    save_all_data(cerebro, artifact_dir)

    # 4. Extract performance metrics
    final_val = cerebro.broker.getvalue()
    sharpe_analysis = strat.analyzers.sharpe.get_analysis()
    drawdown_analysis = strat.analyzers.drawdown.get_analysis()
    returns_analysis = strat.analyzers.returns.get_analysis()

    sharpe = sharpe_analysis.get('sharperatio', 0.0)
    # Handle case where Sharpe might be None
    sharpe = 0.0 if sharpe is None else sharpe
    max_dd = drawdown_analysis.get('max', {}).get('drawdown', 0.0)
    
    # Calculate Compound Annual Growth Rate (CAGR) based on the actual duration of the test
    if len(strat.dates) > 0:
        start_date = strat.dates[0]
        end_date = strat.dates[-1]
        years = (end_date - start_date).days / 365.25
        cagr = ((final_val / 100000.0) ** (1.0 / years) - 1.0) * 100.0
    else:
        cagr = 0.0

    # Log finalized statistics to the console
    print("\n================ BACKTEST RESULTS ================")
    print(f"Final Portfolio Value: ${final_val:,.2f}")
    print(f"Total Return:          {((final_val - START_CASH) / START_CASH):.2%}")  
    print(f"CAGR:                  {cagr:.2f}%")
    print(f"Sharpe Ratio:          {sharpe:.2f}")
    print(f"Max Drawdown:          {max_dd:.2f}%")
    print("==================================================")

    # 5. Generate beautiful premium plot
    if len(strat.dates) > 0:
        # Initialize a custom Matplotlib figure for the performance chart
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.plot(strat.dates, strat.portfolio_values, color='#4F46E5', linewidth=2.5, label=PLOT_LABEL)
        
        # Format axes
        ax.set_title("{}".format(TITLE_PREFIX.format(EVAL_START.year, TO_DATE.year)), fontsize=14, fontweight='bold', pad=15)
        ax.set_xlabel("Date", fontsize=12, labelpad=10)
        ax.set_ylabel("Portfolio Value ($)", fontsize=12, labelpad=10)
        # Format the Y-axis to use commas for currency values
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, loc: "{:,}".format(int(x))))
        
        # Add highlight box with stats
        stats_text = (
            f"Final Value: ${final_val:,.2f}\n"
            f"CAGR: {cagr:.2f}%\n"
            f"Sharpe Ratio: {sharpe:.2f}\n"
            f"Max Drawdown: -{max_dd:.2f}%"
        )
        # Define visual properties for the summary statistics box
        props = dict(boxstyle='round', facecolor='#F3F4F6', alpha=0.9, edgecolor='#E5E7EB')
        # Overlay the summary statistics box onto the chart
        ax.text(0.02, 0.95, stats_text, transform=ax.transAxes, fontsize=11,
                verticalalignment='top', bbox=props)

        plt.tight_layout()
        
        # Define the target directory for saving output artifacts
        artifact_dir = os.path.expanduser(DOWNLOAD_DIR)
        os.makedirs(artifact_dir, exist_ok=True)
        # Export the figure to a high-resolution PNG file
        plot_path = os.path.join(artifact_dir, f"{CHART_PREFIX}{datetime.date.today().strftime('%Y-%m-%d')}.png")
        plt.savefig(plot_path, dpi=300)
        print(f"Performance plot saved to: {plot_path}")
        plt.close()

        # Output CSV containing dates and portfolio values
        save_portfolio_details(strat.dates, strat.portfolio_values, artifact_dir)

def save_portfolio_details(dates, portfolio_values, artifact_dir):
    """
    Saves portfolio values and corresponding dates to a CSV file.
    """
    import pandas as pd
    df = pd.DataFrame({
        "Date": dates,
        "Portfolio Value": portfolio_values
    })
    df["Date"] = pd.to_datetime(df["Date"])
    df.set_index("Date", inplace=True)
    df = df.resample("BME").last().dropna(how="all")
    df.reset_index(inplace=True)
    
    csv_path = os.path.join(artifact_dir, f"{COPYPASTE_FILE}{datetime.date.today().strftime('%Y-%m-%d')}.csv")
    df.to_csv(csv_path, index=False)
    print(f"Portfolio details saved to: {csv_path}")

def save_all_data(cerebro, artifact_dir):
    """
    Saves the historical Close prices for all data feeds in cerebro to a CSV file.
    Extracts data directly from backtrader's in-memory line buffers.
    Must be called after cerebro.run() so that data lines are populated.
    """
    import pandas as pd
    
    all_series = {}
    for data in cerebro.datas:
        ticker = data._name
        size = len(data)
        # Extract dates and close prices from backtrader's line arrays
        dates = [bt.num2date(dt).date() for dt in data.datetime.get(ago=0, size=size)]
        closes = list(data.close.get(ago=0, size=size))
        all_series[ticker] = pd.Series(closes, index=pd.to_datetime(dates), name=ticker)
    
    if all_series:
        df_merged = pd.DataFrame(all_series)
        df_merged.index.name = "Date"
        df_merged.sort_index(inplace=True)
        df_merged = df_merged.resample("BME").last().dropna(how="all")
        df_merged.reset_index(inplace=True)
        
        csv_path = os.path.join(artifact_dir, f"{DATA_FILE}{datetime.date.today().strftime('%Y-%m-%d')}.csv")
        df_merged.to_csv(csv_path, index=False)
        print(f"All data saved to: {csv_path}")

if __name__ == "__main__":
    run_backtest()
