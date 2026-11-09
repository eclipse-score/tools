/**
 * @name High Cyclomatic Complexity in Functions (ASIL)
 * @description Functions exceeding the cyclomatic complexity threshold of the ASIL profile
 * @kind problem
 * @id score/cpp/asil/cyclomatic-complexity-function
 * @problem.severity warning
 * @tags maintainability complexity testability
 */

import cpp
import lib.Checks
import lib.Thresholds

int getComplexityThreshold() { result = Asil::cyclomaticComplexityPerFunction() }

from Function f, int complexity
where Checks::functionCyclomaticComplexity(f, complexity, getComplexityThreshold())
select f,
  "Function has cyclomatic complexity " + complexity + ", exceeding threshold of " +
    getComplexityThreshold()
