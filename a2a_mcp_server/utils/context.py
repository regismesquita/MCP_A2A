"""
Context utility functions for A2A MCP Server.

Contains utility functions for working with FastMCP contexts.
"""

import logging

logger = logging.getLogger(__name__)


async def safe_context_call(ctx, method_name: str, *args, **kwargs):
    """Safely call a context method if it exists, otherwise log a warning."""
    if ctx is None:
        logger.warning(f"Context is None, can't call {method_name}")
        return
    
    method = getattr(ctx, method_name, None)
    if method is None:
        logger.warning(f"Context has no attribute '{method_name}'")
        # For 'complete' method, try to fallback to report_progress(1.0)
        if method_name == "complete" and hasattr(ctx, "report_progress") and callable(ctx.report_progress):
            try:
                logger.info(f"Context missing '{method_name}', falling back to report_progress(1.0)")
                await ctx.report_progress(1.0)
                return
            except Exception as e:
                logger.warning(f"Error in fallback to report_progress: {str(e)}")
        return
    
    try:
        if callable(method):
            return await method(*args, **kwargs)
        else:
            logger.warning(f"Context attribute '{method_name}' is not callable")
            # For 'complete' method, try to fallback to report_progress(1.0)
            if method_name == "complete" and hasattr(ctx, "report_progress") and callable(ctx.report_progress):
                try:
                    logger.info(f"Context attribute '{method_name}' not callable, falling back to report_progress(1.0)")
                    await ctx.report_progress(1.0)
                    return
                except Exception as e:
                    logger.warning(f"Error in fallback to report_progress: {str(e)}")
    except Exception as e:
        logger.warning(f"Error calling context.{method_name}: {str(e)}")
        # For 'complete' method, try to fallback to report_progress(1.0)
        if method_name == "complete" and hasattr(ctx, "report_progress") and callable(ctx.report_progress):
            try:
                logger.info(f"Error calling context.{method_name}, falling back to report_progress(1.0)")
                await ctx.report_progress(1.0)
            except Exception as e2:
                logger.warning(f"Error in fallback to report_progress: {str(e2)}")
