"""
This module defines the PapaBearStrategy, a momentum-based ETF rotation strategy
designed for the backtrader framework.
"""
import datetime
import backtrader as bt

class PapaBearStrategy(bt.Strategy):
    """
    A quantitative momentum strategy that rotates into the top 3 ETFs
    within a given universe based on their trailing performance.
    """
    params = (
        # Day of month to rebalance; currently used as a conceptual placeholder
        ('rebalance_day', 1), 
    )

    def log(self, txt, dt=None):
        """Standard logging function to output strategy activities with timestamps."""
        dt = dt or self.datas[0].datetime.date(0)
        print(f"{dt.isoformat()}: {txt}")

    def __init__(self):
        """Initialize the strategy, identifying the ETF universe and tracking variables."""
        # Store reference to all data feeds provided to Cerebro
        self.etfs = self.datas
        # Track the last month a rebalance occurred to trigger monthly logic
        self.last_rebalance_month = None

    def get_historical_price(self, data, days_ago):
        """
        Retrieves the closing price of a data feed from approximately N calendar days ago.
        
        Args:
            data: The backtrader data feed object.
            days_ago (int): Number of calendar days to look back.
            
        Returns:
            float: The closing price at or just before the target date.
        """
        curr_date = data.datetime.date(0)
        target_date = curr_date - datetime.timedelta(days=days_ago)
        
        # Iterate backwards through available bars to find the closest trading day
        for i in range(len(data)):
            if data.datetime.date(-i) <= target_date:
                return data.close[-i]
        
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
            
        # Fetch historical prices using calendar day offsets
        price_3m = self.get_historical_price(data, 90)
        price_6m = self.get_historical_price(data, 180)
        price_12m = self.get_historical_price(data, 365)
        
        # Compute percentage returns for each period
        gain_3m = (current_price - price_3m) / price_3m if price_3m > 0 else 0
        gain_6m = (current_price - price_6m) / price_6m if price_6m > 0 else 0
        gain_12m = (current_price - price_12m) / price_12m if price_12m > 0 else 0
        
        # Return the simple arithmetic mean of the gains
        avg_gain = (gain_3m + gain_6m + gain_12m) / 3.0
        return avg_gain

    def next(self):
        """Executed on every bar (daily). Checks if a rebalance is due."""
        current_date = self.datas[0].datetime.date(0)
        
        # Trigger rebalance logic if the month has changed
        if self.last_rebalance_month is None or current_date.month != self.last_rebalance_month:
            self.last_rebalance_month = current_date.month
            self.rebalance_portfolio()

    def rebalance_portfolio(self):
        """
        Executes the portfolio rotation logic:
        1. Ranks ETFs by momentum.
        2. Liquidates assets falling out of the top 3.
        3. Allocates capital equally to the new top 3.
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
        self.log(f"Rebalancing. Top 3 ETFs: {top_3_names}")
        for data, mom in rankings[:5]:
            self.log(f"  {data._name}: Momentum = {mom:.2%}")
            
        # 2. Sell any currently held ETFs that are NOT in the top 3
        for data in self.etfs:
            position = self.getposition(data)
            if position.size > 0 and data not in top_3:
                self.log(f"Selling {data._name} (no longer in top 3)")
                # target=0.0 signals to close the position
                self.order_target_percent(data, target=0.0)

        # 3. Buy/Allocate to the top 3 ETFs equally
        # Target is 33% of portfolio value for each of the top 3 ETFs
        target_weight = 1.0 / 3.0
        for data in top_3:
            # Calculate required adjustment to reach 33.3% weight
            self.order_target_percent(data, target=target_weight)
            
    def notify_order(self, order):
        """
        Handles order status notifications from the broker.
        
        Args:
            order: The backtrader order object.
        """
        if order.status in [order.Completed]:
            if order.isbuy():
                self.log(f"BUY EXECUTED, Price: {order.executed.price:.2f}, Cost: {order.executed.value:.2f}, Comm: {order.executed.comm:.2f}")
            else:
                self.log(f"SELL EXECUTED, Price: {order.executed.price:.2f}, Cost: {order.executed.value:.2f}, Comm: {order.executed.comm:.2f}")
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.log(f"ORDER FAILED status: {order.getstatusname()}")
