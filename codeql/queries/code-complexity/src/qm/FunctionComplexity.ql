/**
 * @name High Cyclomatic Complexity in Functions (QM)
 * @description Functions exceeding the cyclomatic complexity threshold of the QM profile
 * @kind problem
 * @id score/cpp/qm/cyclomatic-complexity-function
 * @problem.severity warning
 * @tags maintainability complexity testability
 */

import cpp
import lib.Checks
import lib.Thresholds

int getComplexityThreshold() { result = Qm::cyclomaticComplexityPerFunction() }

from Function f, int complexity
where Checks::functionCyclomaticComplexity(f, complexity, getComplexityThreshold())
select f,
  "Function has cyclomatic complexity " + complexity + ", exceeding threshold of " +
    getComplexityThreshold()
