/**
 * @name Many Public Interfaces per Component (QM)
 * @description Components whose public interface exceeds the threshold of the QM profile
 * @kind problem
 * @id score/cpp/qm/public-interfaces-per-component
 * @problem.severity recommendation
 * @tags maintainability design
 *
 * A component is a class or struct of the analysed sources; its public
 * interfaces are its public member functions. Advisory check: CodeQL maps
 * `recommendation` to the SARIF level `note`, so the finding is reported in
 * Code Scanning and the job summary but does not gate `--fail-on warning`.
 */

import cpp
import lib.Checks
import lib.Thresholds

int getReviewThreshold() { result = Qm::publicInterfacesPerComponent() }

from Class c, int interfaces
where Checks::publicInterfaces(c, interfaces, getReviewThreshold())
select c,
  "Component has " + interfaces +
    " public interfaces, exceeding the QM threshold of " + getReviewThreshold()
