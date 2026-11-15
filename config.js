/*
 * Drakelicit online — site configuration.
 * Loaded by survey.html and results.html before js/drake.js.
 *
 * FORM_URL / FORM_ENTRY stay empty until the Google Form exists. While they are
 * empty nothing is sent anywhere: the test runs, the results page works, and
 * the browser console says "dev mode". That is what you want for local testing.
 */
window.DRAKE_CONFIG = {
  /* https://docs.google.com/forms/d/e/<FORM_ID>/formResponse   (note: formResponse, not viewform) */
  FORM_URL: "",
  /* name of the single long-answer field of the form, e.g. "entry.1234567890" */
  FORM_ENTRY: "",

  /* community curve, written by the GitHub Action (relative path: works on github.io/<repo>/) */
  COMMUNITY_URL: "data/community.json",

  /* shown in the results page footer */
  REPO_URL: "https://github.com/ferdesmello/drakelicit-online",

  /* do not draw the community curve with fewer submissions than this */
  MIN_COMMUNITY: 3,

  /* give up sending after this long (ms) and let the person retry */
  SEND_TIMEOUT_MS: 15000
};
