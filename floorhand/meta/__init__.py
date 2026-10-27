"""Meta clients. Catch BudgetStop at the feature boundary and use offline rules.

No client applies model suggestions to shop data. The only automatic database
writes are the budget ledger in floorhand.models.
"""
from ..models import BudgetStop

__all__ = ["BudgetStop"]
