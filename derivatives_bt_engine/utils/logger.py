import logging
import os
from datetime import datetime

def setup_logger(log_file: str = None):
    """
    Set up logging configuration.
    
    Args:
        log_file: Optional path to log file. If None, uses default name with timestamp.
    """
    if log_file is None:
        # Create logs directory if it doesn't exist
        os.makedirs('logs', exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file = f'logs/backtest_{timestamp}.log'
    
    # Create a logger
    logger = logging.getLogger(__name__)
    
    # Return existing logger if it already has handlers
    if logger.handlers:
        return logger
        
    logger.setLevel(logging.DEBUG)  # Set the logger to the lowest level
    
    # Create file handler for all messages, including DEBUG
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)  # Log all messages to the file

    # Create console handler for INFO level only
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)  # Set to INFO level first
    
    # Create a filter to only allow INFO level messages (but not WARNING or ERROR)
    class InfoFilter(logging.Filter):
        def filter(self, record):
            # Only allow INFO and CRITICAL levels to console (skip WARNING and ERROR)
            return record.levelno == logging.INFO or record.levelno == logging.CRITICAL
    
    # Apply the filter to the console handler
    console_handler.addFilter(InfoFilter())
    
    # Create a formatter and set it for both handlers
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # Add the handlers to the logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger