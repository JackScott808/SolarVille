import logging
from typing import Optional
import time
import board # type: ignore
import digitalio # type: ignore
import adafruit_character_lcd.character_lcd as characterlcd # type: ignore

class LCDManager:
    def __init__(self):
        """Initialize LCD display"""
        try:
            # LCD configuration
            lcd_columns = 16
            lcd_rows = 2

            # Define GPIO pins
            lcd_rs = digitalio.DigitalInOut(board.D25)
            lcd_en = digitalio.DigitalInOut(board.D24)
            lcd_d4 = digitalio.DigitalInOut(board.D23)
            lcd_d5 = digitalio.DigitalInOut(board.D17)
            lcd_d6 = digitalio.DigitalInOut(board.D18)
            lcd_d7 = digitalio.DigitalInOut(board.D22)

            # Initialize the LCD
            self.lcd = characterlcd.Character_LCD_Mono(
                lcd_rs, lcd_en, lcd_d4, lcd_d5, lcd_d6, lcd_d7, 
                lcd_columns, lcd_rows
            )
        except Exception as e:
            logging.error(f"Failed to initialize LCD: {e}")
            raise

    def display_message(self, message: str, duration: Optional[float] = 5.0):
        """Display message on LCD"""
        try:
            self.lcd.clear()
            
            # Format message for 16x2 display
            if len(message) > 16:
                # Split into two lines if message is too long
                message = message[:16] + '\n' + message[16:32]
            
            self.lcd.message = message
            
            if duration:
                time.sleep(duration)
                self.lcd.clear()
        except Exception as e:
            logging.error(f"LCD Display error: {e}")

    def display_trade_info(self, amount: float, price: float):
        """Display trade information"""
        message = f"Trade:{amount:.2f}kWh\nPrice:£{price:.2f}"
        self.display_message(message)

    def display_energy_status(self, demand: float):
        """Display energy status"""
        message = f"Demand:{demand:.2f}kWh"
        self.display_message(message)

    def clear(self):
        """Clear the LCD display"""
        try:
            self.lcd.clear()
        except Exception as e:
            logging.error(f"Error clearing LCD: {e}")