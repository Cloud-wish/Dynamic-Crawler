from __future__ import annotations
import traceback
import sys

def dfs(e: traceback.TracebackException, exc_list: list[str]):
    if e.__cause__ is not None:
        dfs(e.__cause__, exc_list)
    elif (e.__context__ is not None and
        not e.__suppress_context__):
        dfs(e.__context__, exc_list)
    exc_list.append("".join(list(e.format_exception_only())).strip())

def get_exception_list() -> list[str]:
    """
    get the name of exceptions in stack from bottom to top
    """
    e = traceback.TracebackException(*sys.exc_info())
    exc_list = []
    dfs(e, exc_list)
    return exc_list


    