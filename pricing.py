def calculate_price(total_demand, total_supply, buy_grid_price, sell_grid_price):
    """
    Here is a suggestion for the implementation of the calculate_price function.
    It uses <= and >= instead of < and > to avoid the case where SDR is exactly 0 or 1.
    Which was returing an error.
    """
    
    if total_supply <=0:
        return buy_grid_price # If there's no supply, use the buy grid price

    SDR = total_demand / total_supply

    # set P2P price according to Supply-Demand-Ratio
    if SDR <= 0:
        price = sell_grid_price
    elif SDR >= 1:
        price = buy_grid_price
    elif 0 < SDR < 1: # 0 < SDR < 1
        price = sell_grid_price * (1 - SDR) + buy_grid_price * SDR
    else:
        raise ValueError("SDR value is out of expected range.")

    return price
    """
    SDR = total_demand / total_supply if total_supply != 0 else 0

    # set P2P price according to Supply-Demand-Ratio
    if SDR == 0:
        price = buy_grid_price
    elif SDR >= 1:
        price = sell_grid_price
    elif 0 < SDR < 1:
        price = sell_grid_price * SDR + buy_grid_price * (1 - SDR)
    else:
        raise ValueError("SDR value is out of expected range.")
    
    return price
    """