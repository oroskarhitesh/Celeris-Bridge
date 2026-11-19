/** The two health endpoints, which share nothing else with the API. */

export interface Liveness {
  status: "ok";
}

export interface Readiness {
  status: string;
  /** Which record store answered: ``memory`` or ``mongo``. */
  records: string;
}
