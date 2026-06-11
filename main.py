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
ETFS = ["BNDX", "DBC", "EEM", "EFA", "GLD", "IEF", "IWD", "IWF", "IWN", "IWO", "LQD", "TLT", "VNQ"]
# Absolute path to the directory containing historical CSV data
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

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
        # Start recording equity data once the momentum calculation window (2014) is passed
        if current_date >= datetime.date(2015, 1, 1):
            self.dates.append(current_date)
            self.portfolio_values.append(self.broker.getvalue())
            # Continue with standard strategy logic
            super().next()

def run_backtest():
    """
    Configures the Cerebro engine, loads ETF data, executes the backtest, and plots results.
    """
    cerebro = bt.Cerebro()
    
    # 1. Load data feeds (starting from 2014-01-01 to build the 12-month lookback history)
    from_date = datetime.datetime(2014, 1, 1)
    to_date = datetime.datetime.now()
    
    print("Loading ETF historical data feeds using Custom CSV Parser...")
    for ticker in ETFS:
        # Validate existence of data files before loading
        filepath = os.path.join(DATA_DIR, f"{ticker}.csv")
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Data file not found: {filepath}. Please run download_data.py first.")
            
        # Use our CustomCSVData parser to map columns correctly
        data = CustomCSVData(
            dataname=filepath,
            fromdate=from_date,
            todate=to_date
        )
        # Register the data feed with a specific ticker name for internal reference
        cerebro.adddata(data, name=ticker)

    # 2. Configure broker
    cerebro.broker.setcash(100000.0)
    # Apply a 0.1% commission to simulate realistic transaction costs
    cerebro.broker.setcommission(commission=0.001)
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

    print("\nStarting Portfolio Value: $100,000.00")
    print("Running Backtest from 2015-01-01 to Present (utilizing 2014 for momentum buffer)...")
    
    # Run the simulation and capture the resulting strategy instance
    results = cerebro.run()
    strat = results[0]

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
    print(f"Total Return:          {((final_val - 100000.0) / 100000.0):.2%}")
    print(f"CAGR:                  {cagr:.2f}%")
    print(f"Sharpe Ratio:          {sharpe:.2f}")
    print(f"Max Drawdown:          {max_dd:.2f}%")
    print("==================================================")

    # 5. Generate beautiful premium plot
    if len(strat.dates) > 0:
        # Initialize a custom Matplotlib figure for the performance chart
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.plot(strat.dates, strat.portfolio_values, color='#4F46E5', linewidth=2.5, label='Papa Bear Portfolio Value')
        
        # Format axes
        ax.set_title("Livingston's Papa Bear Portfolio Performance (2015 - Present)", fontsize=14, fontweight='bold', pad=15)
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
        artifact_dir = os.path.expanduser("~/Downloads/backtester")
        os.makedirs(artifact_dir, exist_ok=True)
        # Export the figure to a high-resolution PNG file
        plot_path = os.path.join(artifact_dir, "portfolio_performance.png")
        plt.savefig(plot_path, dpi=300)
        print(f"Performance plot saved to: {plot_path}")
        plt.close()

if __name__ == "__main__":
    run_backtest()
