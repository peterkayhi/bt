"""
This module defines the PapaBearStrategy, a momentum-based ETF rotation strategy
designed for the backtrader framework.
"""
from asyncio import selector_events
from asyncio import selector_events
import datetime
import logging
from pathlib import Path
import backtrader as bt
import pandas as pd


class PapaBearStrategy(bt.Strategy):
    """
    A quantitative momentum strategy that rotates into the top 3 ETFs
    within a given universe based on their trailing performance.
    """
    # Strategy parameters
    params = (
        # Day of month to rebalance; currently used as a conceptual placeholder
        ('rebalance_day', 1), 
        # Logger prefix/identifier. If None, it will be dynamically generated using portfolio_name.
        ('log_name', None),
        # Portfolio name being backtested
        ('portfolio_name', 'Papa Bear'),
        # Directory where log files are stored
        ('log_dir', str(Path.home() / "Downloads" / "logFiles")),
        # Cash safety buffer to prevent margin/insufficient cash failures
        # careful with this vs. any commission charges - check log for errors if you change it
        ('cash_buffer', 0.01),
        # Rebalancing threshold trigger (percent difference)
        ('rebalance_trigger', 0.2),
        # Rebalancing threshold target (percent difference)
        ('rebalance_target', 0.1),
        # Lookback windows (in days) to calculate momentum
        ('lookbacks', [63, 126, 252]),
    )

    def log(self, txt, dt=None, level='info'):    
        """Log a message with the strategy's current datetime.

        Args:
            txt (str): The log message text to record.
            dt (datetime.date, optional): The date to associate with the log entry.
                If not provided, the date of the current bar from the primary data feed is used.
            level (str or int, optional): The log level or type. Defaults to 'info'.
        """
        dt = dt or self.datas[0].datetime.date(0)
        
        # Register custom levels dynamically if they aren't already defined
        for lvl_name, lvl_val in [('REBAL', 21), ('TRIG', 22), ('BUY', 23), ('SELL', 24), ('DATE', 25), ('PORT', 26)]:
            if not hasattr(logging, lvl_name):
                logging.addLevelName(lvl_val, lvl_name)
                setattr(logging, lvl_name, lvl_val)
        
        if isinstance(level, str):
            try:
                level = getattr(logging, level.upper())
            except AttributeError:
                level = logging.INFO
                
        self.logger.log(level, f"{dt.isoformat()}: {txt}")

    def __init__(self):
        """Initialize the strategy instance.

        Sets up the logger and its handlers (file handler and console stream handler)
        according to the configured strategy parameters.
        """
        # Determine dynamic log name
        if self.p.log_name is None:
            clean_name = self.p.portfolio_name.lower().replace(" ", "_").replace("&", "and")
            log_name = f"papa_bear_{clean_name}_{datetime.date.today().strftime('%Y-%m-%d')}"
        else:
            log_name = self.p.log_name

        # Setup logging
        self.logger = logging.getLogger(log_name)
        logdir = Path(self.p.log_dir)
        logdir.mkdir(parents=True, exist_ok=True)
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            prefix = f"[{self.p.portfolio_name}] " if self.p.portfolio_name else ""
            formatter = logging.Formatter(f'%(asctime)s %(levelname)-8s {prefix}%(message)s', datefmt='%Y-%m-%d %H:%M:%S')
            for handler in [logging.FileHandler(f"{logdir}/{log_name}.app.log", mode='a', encoding='utf-8'), logging.StreamHandler()]:
                handler.setFormatter(formatter)
                self.logger.addHandler(handler)

        # Store reference to all data feeds provided to Cerebro
        self.etfs = self.datas
        # Track the last month a rebalance occurred to trigger monthly logic
        self.last_rebalance_month = None
        # Track the top 3 ETFs from the last rebalance
        self.last_top_3 = None

    def get_historical_price(self, data, lookback):
        """
        Retrieves the closing price of a data feed from lookback trading days ago.
        
        Args:
            data: The backtrader data feed object.
            lookback (int): Number of trading days to look back.
            
        Returns:
            float: The closing price lookback trading days ago, or fallback to the first available bar.
        """
        if len(data) > lookback:
            return data.close[-lookback]
        # Fallback: return the first available bar if the requested history is missing
        return data.close[-len(data) + 1]

    def calculate_momentum(self, data):
        """
        Calculates a momentum score based on the average returns of 3, 6, and 12 months.
        
        Args:
            data: The backtrader data feed for a specific ETF.
            
        Returns:
            float: The average percentage gain across the three windows.
        """
        current_price = data.close[0]
        # Avoid division by zero or processing invalid data
        if current_price <= 0:
            return -999.0
            
        if not self.p.lookbacks:
            return 0.0

        gains = [
            (current_price - hist_price) / hist_price if (hist_price := self.get_historical_price(data, lookback)) > 0 else 0
            for lookback in self.p.lookbacks
        ]
        return sum(gains) / len(gains)

    def next(self):
        """Executed on every bar (daily). Checks if a rebalance is due."""
        current_date = self.datas[0].datetime.date(0)
        
        # Check if there is the next date will be a month change, i.e. this is the last day of the month we have in the data
        month_change = False
        if len(self.datas[0]) > 1 and len(self.datas[0]) < self.datas[0].buflen():
            # look to the next date in the data
            next_date = self.datas[0].datetime.date(1)
            
            month_change = (current_date.month != next_date.month)
            
        self.log(f"Month Change: {month_change}", dt=current_date, level='DATE')
        
        # Trigger rebalance on month change
        if month_change:
            self.rebalance_portfolio()

    def log_order_details(self, data, target):
        """
        Logs details of an order before calling order_target_percent.
        """
        price = data.close[0]
        portfolio_value = self.broker.getvalue()
        cash = self.broker.getcash()
        current_size = self.getposition(data).size
        target_size = int((portfolio_value * target) / price) if price > 0 else 0
        qty = target_size - current_size
        total_buy = qty * price
        log_level = "BUY" if total_buy > 0 else "SELL"
        self.log(
            f"Order Details - ETF: {data._name}: Price: {price:.2f}: Qty: {qty}: "
            f"Total Buy: {total_buy:.2f}",
            level=log_level
        )
        self.log(f"Portfolio: {portfolio_value:.2f}: Cash: {cash:.2f}", level="PORT")

    def rebalance_portfolio(self):
        """
        Executes the portfolio rotation logic:
        1. Ranks ETFs by momentum.
        2. Liquidates assets falling out of the top 3.
        3. Rebalances underperforming/overperforming holdings based on trigger and target.
        """
        # 1. Calculate momentum for all ETFs
        rankings = []
        for data in self.etfs:
            # Ensure we have at least one data point before calculating
            if len(data) > 0:
                momentum = self.calculate_momentum(data)
                rankings.append((data, momentum))
        
        # Sort the universe: highest momentum first
        rankings.sort(key=lambda x: x[1], reverse=True)
        
        # Select the top 3 performing ETFs
        top_3 = [r[0] for r in rankings[:3]]
        top_3_names = [d._name for d in top_3]
        
        # Log rebalancing details for transparency
        self.log(f"Rebalance check. Top 3 ETFs: {top_3_names}")
        for data, mom in rankings[:5]:
            self.log(f"  {data._name}: Momentum = {mom:.2%}")
            
        # 2. Sell any currently held ETFs that are NOT in the top 3
        for data in self.etfs:
            position = self.getposition(data)
            if position.size > 0 and data not in top_3:
                self.log(f"Rebalancing: Selling {data._name} (no longer in top 3)", level="REBAL")
                # target=0.0 signals to close the position
                self.log_order_details(data, target=0.0)
                self.order_target_percent(data, target=0.0)

        portfolio_value = self.broker.getvalue()

        # 3. Buy/Allocate or rebalance to the top 3 ETFs
        if not self.last_top_3 or len(self.last_top_3) < 3:
            # First time: allocate equally (adjust for cash buffer)
            target_weight = (1.0 - self.p.cash_buffer) / 3.0
            self.log("Initial equal allocation for top 3 ETFs")
            for data in top_3:
                self.log_order_details(data, target=target_weight)
                self.order_target_percent(data, target=target_weight)
        else:
            # Map top_3 values using Pass 1 and Pass 2 from self.last_top_3
            prev_vals = []
            for d in self.last_top_3:
                val = self.getposition(d).size * d.close[0]
                prev_vals.append(val)
                
            new_vals = [None, None, None]
            used_prev_idx = []
            
            # Pass 1: Preserve tickers that remain in the top 3
            for k in range(3):
                curr_etf = top_3[k]
                if curr_etf in self.last_top_3:
                    prev_idx = self.last_top_3.index(curr_etf)
                    new_vals[k] = prev_vals[prev_idx]
                    used_prev_idx.append(prev_idx)
                    
            # Pass 2: Rotations (fill empty slots using dropped tickers' values)
            unused_prev_idx = [idx for idx in range(3) if idx not in used_prev_idx]
            for k in range(3):
                if new_vals[k] is None:
                    if k in unused_prev_idx:
                        funding_idx = k
                        unused_prev_idx.remove(k)
                    else:
                        funding_idx = unused_prev_idx.pop(0)
                    new_vals[k] = prev_vals[funding_idx]
            
            # Now we check if we exceed the rebalance trigger
            v_max = max(new_vals)
            v_min = min(new_vals)
            
            if v_min > 0:
                percent_delta = (v_max - v_min) / v_min
            else:
                percent_delta = float('inf')
                
            # Initialize target values with the mapped new_vals
            target_vals = list(new_vals)
            max_idx = new_vals.index(v_max)
            min_idx = new_vals.index(v_min)
            
            rebalanced = False
            if percent_delta >= self.p.rebalance_trigger:
                self.log(f"Trigger exceeded: percent_delta={percent_delta:.2%} >= trigger={self.p.rebalance_trigger:.2%}. Rebalancing largest and smallest holdings.", level="TRIG")
                # Calculate delta to transfer from largest to smallest
                delta = (v_max - v_min * (1.0 + self.p.rebalance_target)) / (2.0 + self.p.rebalance_target)
                
                target_vals[max_idx] = v_max - delta
                target_vals[min_idx] = v_min + delta
                rebalanced = True
            else:
                self.log(f"NoRebalance triggered: percent_delta={percent_delta:.2%} < trigger={self.p.rebalance_trigger:.2%}.")
                
            # Execute the orders:
            # 1. If rebalanced, execute orders for largest and smallest
            # 2. If any ETF is a rotation (was not in last_top_3), execute order to establish position
            for k in range(3):
                etf = top_3[k]
                is_rotation = (etf not in self.last_top_3)
                is_rebalanced_leg = rebalanced and (k in (max_idx, min_idx))
                
                if is_rotation or is_rebalanced_leg:
                    target_weight = target_vals[k] / portfolio_value if portfolio_value > 0 else 0.0
                    self.log_order_details(etf, target=target_weight)
                    self.order_target_percent(etf, target=target_weight)
                
        # Update last top 3
        self.last_top_3 = top_3
            
    def notify_order(self, order):
        """
        Handles order status notifications from the broker.
        
        Args:
            order: The backtrader order object.
        """
        if order.status in [order.Completed]:
            if order.isbuy():
                self.log(f"BUY EXECUTED, Price: {order.executed.price:.2f}: Cost: {order.executed.value:.2f}: Comm: {order.executed.comm:.2f}")
                self.log(f"Portfolio: {self.broker.getvalue():.2f}: Cash: {self.broker.getcash():.2f}", level="PORT")
            else:
                self.log(f"SELL EXECUTED, Price: {order.executed.price:.2f}: Cost: {order.executed.value:.2f}: Comm: {order.executed.comm:.2f}")
                self.log(f"Portfolio: {self.broker.getvalue():.2f}: Cash: {self.broker.getcash():.2f}", level="PORT")
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.log(f"ORDER FAILED status: {order.getstatusname()}: Portfolio Value: {self.broker.getvalue():.2f}: Cash: {self.broker.getcash():.2f}", level='error')
