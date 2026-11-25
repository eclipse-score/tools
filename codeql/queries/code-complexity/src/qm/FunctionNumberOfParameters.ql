/**
 * @name High Number of Parameters per Function (QM)
 * @description Functions exceeding the parameter count threshold of the QM profile
 * @kind problem
 * @id score/cpp/qm/number-of-parameters-per-function
 * @problem.severity warning
 * @tags maintainability design readability
 */

import cpp
import lib.Checks
import lib.Thresholds

int getParameterThreshold() { result = Qm::parametersPerFunction() }

from Function f, int params
where Checks::functionParameters(f, params, getParameterThreshold())
select f,
  "Function has " + params + " parameters, exceeding threshold of " +
    getParameterThreshold()
