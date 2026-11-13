/**
 * @name Number of Goto Statements in Functions
 * @description Functions with goto statement count exceeding threshold
 * @kind problem
 * @id score/cpp/goto-statements
 * @problem.severity warning
 * @tags maintainability complexity readability
 */

import cpp
import lib.Checks

int getGotoStatementsThreshold() { result = 0 }

from Function f, int gotoStatements
where Checks::functionGotoStatements(f, gotoStatements, getGotoStatementsThreshold())
select f,
  "Function has " + gotoStatements + " goto statements, exceeding threshold of " +
    getGotoStatementsThreshold()
