""" 
momda - forked papaSrc and added moving day average logic

"""

import pandas as pd
import yfinance as yf
import numpy as np
import logging
from datetime import datetime
from pathlib import Path
from yfcache import yfcache

# =============================================================================
# CONFIGURATION - change these as needed
# =============================================================================
# Official Papa Bear 13 ETFs (straight from muscularportfolios.com)

def run_momda (
    tickers_param: list[str] = [
        'VTV', 'VUG', 'VIOV', 'VIOG', 'VEA', 'VWO', 'VNQ',
        'PDBC', 'IAU', 'EDV', 'VGIT', 'VCLT', 'BNDX'
    ],
    mom_days: list[int] = [63, 126, 252], # trading days to average over
    start_date: str  = (datetime.today() - pd.Timedelta(weeks=532)).strftime("%Y-%m-%d"),# approx 10 yrs and 3 months go
    end_date: str    = (pd.Timestamp.today() - pd.offsets.BMonthEnd(1)).strftime("%Y-%m-%d"), # previous month business end date
    top_assets: int = 3, # how many top assets to balance?
    value_start: float = 100_000, # starting porfolio value
    rebalance_trigger: float  = .2, # what's the biggest bucket delta we'll take before we rebalance
    rebalance_target: float  = .1, # when rebalancing, what's the target to get to?
    mda_param: int = 0, # non-zero values kick in the days to compute moving average
    cash_etf: str = 'BIL',
    file_prefix: str  = "momda",  # suffix gets appended to each csv file
    verbose: bool = False, # outputs lots of csv files along the way
    skip_cache: bool = False, # use the cache to minimize Yahoo api calls
    output_dir_param: str  = str(Path.home() / "Downloads" / "backtestFiles") # directory holding csvs
) -> None:

    # set/verify directory
    output_dir = Path(output_dir_param)
    output_dir.mkdir(exist_ok=True)

    # Setup logging
    logger = logging.getLogger(file_prefix)
    logdir = Path.home() / "Downloads" / "logFiles"
    logdir.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        for handler in [logging.FileHandler(f"{logdir}/{file_prefix}.app.log", mode='a', encoding='utf-8'), logging.StreamHandler()]:
            handler.setFormatter(formatter)
            logger.addHandler(handler)
    logger.info(f"Starting MOMDA backtest with tickers: {tickers_param}, MDA lookback: {mda_param} days, top assets: {top_assets}, from {start_date} to {end_date}")

    # simple output to csv function
    def save_csv(
        df: pd.DataFrame, # holds what we'll output
        file_name: str = "csvFile",
        dir: str = output_dir, # pathname
        pref: str = file_prefix # prefix - typicaly the project name
        ) -> None:
        df.to_csv(f"{dir}/{pref}{file_name}.csv")


    # fix top_assets in case we have less tickers than buckets to hold
    top_assets = min(top_assets, len(tickers_param))
    # =============================================================================
    # DOWNLOAD WITH MOMENTUM LOOKBACK BUFFER
    # =============================================================================
    # Fix for the bug you spotted: pull extra history so 12-month momentum works from day 1
    download_start = (pd.to_datetime(start_date) - pd.DateOffset(months=15)).strftime("%Y-%m-%d")
    cache = yfcache() # get our Yahoo data via caching - reduces api call guilt
    dataget = cache.get( tickers_param, download_start, end_date, skip_cache)
    data = dataget.final_df
    logger.info(f"DL tickers missed: {dataget.missed_tickers}, Needed starts: {dataget.needed_starts}")

    csvFileName = "yfdata"
    if verbose: save_csv(data,"yfdata")

    if mda_param > 0:  # if we're doing mda we'll need cash accounts
        if skip_cache:
            cash_df = yf.download(
                cash_etf,
                start=download_start,
                end=end_date,
                auto_adjust=True,      # fully adjusted OHLC (Close column is perfect)
                progress=False
            )["Close"]
        else:
            cashget = cache.get( [cash_etf], download_start, end_date)
            cash_df = cashget.final_df
            logger.info(f"Cash Missed tickers: {cashget.missed_tickers}, Needed starts: {cashget.needed_starts}")

        cash_cols = [f"{cash_etf}{i}" for i in range(1, top_assets + 1)] # labels for cash df
        data = data.assign(**{col: cash_df for col in cash_cols}) # add a cash account ticker for each bucket (top_assets) to the portfolio - this way under the worst month cash will show up as the best momentum performer.
        mda = data.rolling(mda_param).mean() # store moving day average in another dataframt
        above_mda = data > mda # and yet another mask to hold true if closing price is above mda
        above_mda[cash_cols] = True #force the cash accounts to be above closing price so that we know that at least those accounts will get selected.
    else: # we're not doing mda
        above_mda = data != None # set the mask to all true so nothing gets affected
    above_mda = above_mda.resample("BME").last().dropna(how="all") # we'll only need the month-end mask
    if not above_mda.empty and above_mda.index[-1] > data.index[-1]:
        above_mda = above_mda.iloc[:-1]

    # =============================================================================
    # Resample to month-end last trading day
    # .resample("ME") groups by month-end, .last() takes the last price of that month.
    # .dropna(how="all") removes rows where all tickers are NaN (e.g., if month-end was a holiday).
    # .resample is an intermediate step to ensure we have clean month-end data for momentum calculations and rebalancing - you have to add an aggregation method like .last() to get a single price per month, and dropna to handle any missing data at month-ends. how="all" means it will only drop rows where all columns are NaN, which is what we want in case some tickers have missing data but others don't.
    # use BME instead of ME to get the last business day of the month, which is more accurate for trading purposes since the actual month-end might be a weekend or holiday. BME stands for Business Month End.
    monthly_prices = data.resample("BME").last().dropna(how="all")
    if not monthly_prices.empty and monthly_prices.index[-1] > data.index[-1]:
        monthly_prices = monthly_prices.iloc[:-1]

    if verbose: save_csv(monthly_prices,"monthPrices") # save monthly data for debugging

    # =============================================================================
    # PAPA BEAR MOMENTUM: average of 3/6/12-month returns
    # .pct_change(periods=3) gives 3-month return, etc. Returns are in decimal (e.g., 0.05 for 5%).
    # .pct_change() looks back by N rows as defined by periods, so it calculates the return from that point to the current row.
    # we need to lookback 63 days vs. 3 months so we'll look through the data df and then resample to business month end after calculating the pct change. This way we ensure that the momentum is calculated based on the actual daily data, and then we can align it with the month-end dates for our strategy.

    # to calculate 62 days back we need data.pct_change(periods=62) 
    # =============================================================================    
    # Calculate momentum for each period and average them
    mom_list = [data.pct_change(periods=d).resample("BME").last() for d in mom_days]
    # Trim partial months from momentum to align with price data
    mom_list = [m.iloc[:-1] if not m.empty and m.index[-1] > data.index[-1] else m for m in mom_list]
    avg_momentum = sum(mom_list) / len(mom_days)

    # =============================================================================
    # PORTFOLIO: top 3 equal-weighted each month (rebalance on month-end)
    # We create a weights DataFrame initialized to 0.0, then loop through each month starting from the second row (since the first month has no prior momentum). For each month, we look at the previous month's momentum, find the top 3 tickers that are also above their mda (if that feature is turned on and store tickers and values for later use 
    # the .iloc[i-1] gets the previous month's momentum, .nlargest(3) finds the top 3 tickers, 
    # =============================================================================


    ticker_cols = [f"t{i}" for i in range(1, top_assets + 1)] # build list of ticker cols e.g. t1, t2, t3 etc
    price_cols = [f"p{i}" for i in range(1, top_assets + 1)]  # list of price cols e.g. p1, p2, p3, etc

    # create empty dataframes to handle top tickers and their prices

    top_ticks = pd.DataFrame(index=avg_momentum.index, columns=ticker_cols)
    top_close = pd.DataFrame(index=avg_momentum.index, columns=price_cols)


    for i in range(1, len(avg_momentum) + 1 ):  # doing +1 so we populate the last row
        # For each month starting from the second row (i=1), we look back at the previous month's momentum to determine the top tickers. 
        #
        # the len() function in panda returns the number of rows in avg_momentum, so the loop iterates through each month starting from the second one (since the first month has no prior momentum data). Inside the loop, we use .iloc[i-1] to access the previous month's momentum data, find the top 3 tickers with .nlargest(3), and then store the values for later use 

        prev_mom = avg_momentum.iloc[i-1] # Get the previous month's momentum
        mda_mask = above_mda.loc[prev_mom.name] # and that month's mda mask
        ticks = prev_mom[mda_mask].nlargest(top_assets) # get our top tickers that are above the mda in a series that includes the date
        top_ticks.loc[ticks.name, ticker_cols] = ticks.index # save those tickers
        prices = monthly_prices.loc[ticks.name][ticks.index] # then lookup the prices for the date [ticks.name] for the tickers [ticks.index]
        top_close.loc[ticks.name, price_cols] = prices.values # and store those prices in their corresponding puka 
        pass # end of loop

    # trim out dates before the actual start date
    top_close = top_close[top_close.index > pd.to_datetime(start_date)]
    # save 'em
    if verbose: save_csv(top_ticks,"topTics")
    if verbose: save_csv(top_close,"topClose")


    #===========
    # calculate holdings

    # create value and shares tables
    val_cols = [f"v{i}" for i in range(1, top_assets + 1)]  # value of shares e.g. v1, v2, v3
    share_cols = [f"s{i}" for i in range(1, top_assets + 1)]  # shares held  e.g. s1, s2, s3

    value = pd.DataFrame(index=avg_momentum.index, columns=val_cols)
    shares = pd.DataFrame(index=avg_momentum.index, columns=share_cols)

    # build df of any month where tickers have changed 
    # 

    any_changes = top_ticks.ne(top_ticks.shift(1)).any(axis=1)

    if verbose: save_csv(any_changes,"anyChanges")

    # Main simulation loop: Iterate through each month and its target ticker prices
    for month, row_adj_close in top_close.iterrows():
        i = top_close.index.get_loc(month) # get row of current month
        # Handle the start of the backtest
        if i == 0:
            # Distribute initial capital equally across all buckets
            value.loc[month] = value_start / top_assets
            # Calculate shares to buy based on current month's prices
            shares.loc[month, share_cols] = value.loc[month].values / row_adj_close.values
            logger.info(f"Initial allocation on {month.strftime('%Y-%m-%d')}")
        else:
            # Process subsequent months: identify previous holdings and current targets
            prev_month = top_close.index[i-1] #previous month top closing prices
            p_ticks = top_ticks.loc[prev_month] # previous top tickers
            p_shares = shares.loc[prev_month] # previous shares held 
            c_ticks = top_ticks.loc[month] # current top tickers 
            
            # Mark-to-Market: Calculate the current value of last month's shares at today's prices
            # mtm_prev_vals represents the "cash proceeds" available from each previous bucket
            mtm_prev_vals = p_shares.values * monthly_prices.loc[month][p_ticks.values].values
            
            # Temporary containers for current month calculations
            temp_shares = pd.Series(index=share_cols, dtype=float)
            temp_values = pd.Series(index=val_cols, dtype=float)
            
            used_prev_idx = [] # Tracks which previous buckets were kept or moved
            
            # Pass 1: Preserve tickers that remain in the 'top_assets' list
            for k in range(top_assets):
                curr_ticker = c_ticks.iloc[k]
                if curr_ticker in p_ticks.values: #current ticker in previous top tickers? 
                    # Find where this ticker was located last month
                    prev_idx = list(p_ticks.values).index(curr_ticker) # find position of curr_ticker inside prev month ticks. list() converts numpy array into std python list
                    # Move existing shares from that preivous position and their current value to the new bucket position k
                    temp_shares.iloc[k] = p_shares.iloc[prev_idx] 
                    temp_values.iloc[k] = mtm_prev_vals[prev_idx]
                    used_prev_idx.append(prev_idx) # and remember this position has been used. 
            pass # by end of loop we've saved (in used_prev_idx)which positions had existing tickers,  identified all tickers that exist in both months and we've moved their shares and values to their corresponding buckets in this month.  e.g. if ticker IAU was in position 2 last month and it's in position 1 this month, we've moved the shares and values from bucket 2 to bucket 1.           
            
            # Pass 2: Fill buckets for new tickers (rotations) using capital from dropped tickers
            unused_prev_idx = [idx for idx in range(top_assets) if idx not in used_prev_idx] # create a list of unused buckets from previous loop, i.e. tickers that existed in previous month but in current one - and we'll use that capital to buy this month's top tickers that weren't in last month's top.
            
            for k in range(top_assets):
                if pd.isna(temp_shares.iloc[k]):
                    # If this bucket is empty, a rotation is required. 
                    # Preference: use capital from the same bucket index if it was dropped.
                    if k in unused_prev_idx:  # is this an unused bucket?
                        funding_idx = k # we'll take it. 
                        unused_prev_idx.remove(k) # and remove it from the list so we don't use it again
                    else:
                        # Otherwise, take capital from the first available dropped ticker bucket.
                        funding_idx = unused_prev_idx.pop(0) # pop off top of stack 
                    
                    # Reinvest the proceeds from the dropped ticker into the new ticker
                    temp_values.iloc[k] = mtm_prev_vals[funding_idx] # value of dropped ticker from last month 
                    temp_shares.iloc[k] = temp_values.iloc[k] / row_adj_close.iloc[k] # buy shares of new ticker at current price
            
            # Update final DataFrames with the results of Pass 1 and Pass 2
            value.loc[month] = temp_values.values
            shares.loc[month] = temp_shares.values
            
            if any_changes[month]:
                logger.info(f"Tickers changed on {month.strftime('%Y-%m-%d')}")

        # Rebalancing: Check if the value distribution has drifted too far from equal weighting
        current_vals = value.loc[month].astype(float)
        # Check if the percentage difference between the maximum and minimum bucket values exceeds the rebalance_trigger.
        # The formula (max - min) / min calculates the percentage difference relative to the smallest value.
        if (current_vals.max() - current_vals.min()) / current_vals.min() >= rebalance_trigger:
            # Log that a minimal rebalancing event is occurring, including the date.
            logger.info(f"Rebalancing on {month.strftime('%Y-%m-%d')}")
            
            # Iteratively move capital from the highest value bucket to the lowest value bucket 
            # until the difference between any two assets is within the trigger threshold.
            # This loop attempts to converge on the desired distribution. A safety limit (100 iterations)
            # is set to prevent infinite loops in case of floating-point precision issues or complex scenarios.
            for _ in range(100): # Safety limit to prevent infinite loops
                # Identify the ticker (column label) with the maximum value in the current portfolio.
                v_max_label = current_vals.idxmax()
                # Identify the ticker (column label) with the minimum value in the current portfolio.
                v_min_label = current_vals.idxmin()
                
                # Check if the current difference between the max and min values is already within the trigger.
                # A small epsilon (1e-9) is added for floating-point comparison robustness.
                if (current_vals[v_max_label] - current_vals[v_min_label]) / current_vals[v_min_label] <= rebalance_target + 1e-9:
                    # If the condition is met, the rebalancing is complete for this month, so break the loop.
                    break
                
                # Calculate the exact amount (delta) to move so that: (max - delta) = (min + delta) * (1 + target)
                # This formula ensures that after moving 'delta', the new max value is (1 + rebalance_trigger) times the new min value.
                delta = (current_vals[v_max_label] - current_vals[v_min_label] * (1 + rebalance_target)) / (2 + rebalance_target)
                # Decrease the value of the over-weighted asset by 'delta'.
                current_vals[v_max_label] -= delta
                # Increase the value of the under-weighted asset by 'delta'.
                current_vals[v_min_label] += delta

            # Update the portfolio 'value' DataFrame for the current month with the newly rebalanced values.
            value.loc[month] = current_vals
            # Update share counts to reflect the adjusted values
            # Recalculate the number of shares for each asset based on their new rebalanced values and current closing prices.
            shares.loc[month, share_cols] = value.loc[month].values / row_adj_close.values

    if verbose: save_csv(value,"value")
    if verbose: save_csv(shares,"shares")

    #==========================
    # create portfolio list for the last day 

    day_list = [data.pct_change(periods=d).resample("B").last() for d in mom_days] # get daily momentum buckets
    avg_daily_momentum = sum(day_list) / len(mom_days) # and average them
    last_day = pd.to_datetime(avg_daily_momentum.iloc[-1].name) # save the last date for later use
    today_momentum = (avg_daily_momentum.loc[last_day] * 100).round(2) # extract last day's momentum and convert to pcnt
    today_prices = data.loc[pd.to_datetime(today_momentum.name)][today_momentum.index] # get today's closing prices
    today_report = pd.DataFrame({ # combine the two into 1 report dataframe
        'Avg Momentum': today_momentum,      
        'Adj Close': today_prices    
    })
    today_report = today_report.sort_values('Avg Momentum', ascending=False) # sort by momentum
    # format the columns
    today_report["Adj Close"] = today_report["Adj Close"].map("${:,.2f}".format)
    today_report["Avg Momentum"] = today_report["Avg Momentum"].map("{:.2f}%".format)
    # twist it around to get more of a report look 


    #===========
    # finalize data for copy/paste friendly format

    copy_paste = value.sum(axis=1) # we just care about the monthly total portfolio value
    copy_paste = value[value.index > pd.to_datetime(start_date)].sum(axis=1) # only total portfolio value from the original start date specified

    #copy_paste = copy_paste.rename(columns={copy_paste.columns[0]: f"{file_prefix}"})
    copy_paste.rename(f"{file_prefix}", inplace=True)
    csvFileName = "CopyPaste"
    logger.info(f"Exporting Values to {file_prefix}{csvFileName}.csv")
    save_csv(copy_paste,"CopyPaste")

    #====================
    # create a stats df
    # Calculate monthly percentage returns and drop the first NaN value
    returns = copy_paste.pct_change().dropna()
    # Calculate the running peak value of the portfolio for drawdown analysis
    cum_max = copy_paste.cummax()
    # Calculate monthly drawdowns as the percentage decline from the running peak
    drawdowns = (copy_paste / cum_max) - 1
    # Calculate the total number of years in the backtest for annualization
    n_years = (copy_paste.index[-1] - copy_paste.index[0]).days / 365.25

    # Construct the PortStats DataFrame with key risk and return metrics
    port_stats = pd.DataFrame({
        f"{file_prefix}": [ #set the column name to the file prefix for easy identification
            returns.std() * np.sqrt(12), # Annualized Standard Deviation
            np.sqrt(np.mean(drawdowns**2)), # Ulcer Index: Root Mean Square of drawdowns
            (returns.mean() * 12) / (returns.std() * np.sqrt(12)), # Annualized Sharpe Ratio (assuming 0% risk-free rate)
            (1 - (cum_max / copy_paste)).min(), # Maximum Drawdown using the specific requested formula
            (copy_paste.iloc[-1] / copy_paste.iloc[0]) ** (1 / n_years) - 1 # Compound Annual Growth Rate (CAGR)
        ]
    }, index=[
        "Standard Deviation",
        "Ulcer Index",
        "Sharpe Ratio",
        "Maximum Drawdown",
        "CAGR"
    ])
    # Save the statistics to a CSV file named PortStats
    save_csv(port_stats, "PortStats")

    #==============
    # print the today report
    logger.info(f"Asset Mix on {last_day.strftime('%m/%d/%Y')} Closing:")
    logger.info(today_report)
    # now save to csv

    today_report.index.name = f"{file_prefix}:{last_day.strftime('%m/%d/%Y')}" # give index a good label
    save_csv(today_report, "TodayReport")

    logger.info("Done!")