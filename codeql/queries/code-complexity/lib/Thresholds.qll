/**
 * Complexity thresholds per safety profile.
 *
 * CodeQL compiles thresholds into the queries, so each profile has its own query
 * set (`src/qm`, `src/asil`) that reads its thresholds from the module below.
 * Keep these values in sync with `config/reports.toml`, `README.md` and the
 * `static-analysis-codeql` workflow documentation.
 */

/**
 * Thresholds of the `QM` profile. QM code has no safety requirements, so the
 * limits are the looser ones.
 */
module Qm {
  /** Maximum lines of code per function. */
  int linesOfCodePerFunction() { result = 200 }

  /** Maximum lines of code per file. */
  int linesOfCodePerFile() { result = 4000 }

  /** Maximum cyclomatic complexity per function. */
  int cyclomaticComplexityPerFunction() { result = 20 }

  /** Maximum number of parameters per function. */
  int parametersPerFunction() { result = 7 }

  /** Maximum number of public interfaces (member functions) per component. */
  int publicInterfacesPerComponent() { result = 10 }
}

/**
 * Thresholds of the `ASIL` profile (ASIL A-D). Applies to safety-related code
 * and is therefore stricter than {@link Qm}.
 */
module Asil {
  /** Maximum lines of code per function. */
  int linesOfCodePerFunction() { result = 100 }

  /** Maximum lines of code per file. */
  int linesOfCodePerFile() { result = 2000 }

  /** Maximum cyclomatic complexity per function. */
  int cyclomaticComplexityPerFunction() { result = 10 }

  /** Maximum number of parameters per function. */
  int parametersPerFunction() { result = 5 }

  /** Maximum number of public interfaces (member functions) per component. */
  int publicInterfacesPerComponent() { result = 3 }
}
