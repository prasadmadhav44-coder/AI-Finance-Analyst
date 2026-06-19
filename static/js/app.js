(() => {
    "use strict";

    const form = document.getElementById("analyzeForm");
    const queryInput = document.getElementById("query");
    const charCount = document.getElementById("charCount");
    const queryError = document.getElementById("queryError");
    const loader = document.getElementById("loader");
    const runBtn = document.getElementById("runBtn");
    const runBtnLabel = document.getElementById("runBtnLabel");
    const connectionStatus = document.getElementById("connectionStatus");
    const toast = document.getElementById("toast");

    const MAX_QUERY_LENGTH = 1000;
    const REQUEST_TIMEOUT_MS = 60_000;

    let activeController = null;
    let toastTimeoutId = null;

    // -------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------
    function showToast(message) {
        toast.textContent = message;
        toast.classList.add("is-visible");
        if (toastTimeoutId) clearTimeout(toastTimeoutId);
        toastTimeoutId = setTimeout(() => {
            toast.classList.remove("is-visible");
        }, 5000);
    }

    function setStatus(state, label) {
        connectionStatus.textContent = label;
        connectionStatus.classList.remove("status-pill--idle", "status-pill--busy", "status-pill--error");
        connectionStatus.classList.add(`status-pill--${state}`);
    }

    function setFieldError(message) {
        if (!message) {
            queryError.classList.add("hidden");
            queryError.textContent = "";
            queryInput.removeAttribute("aria-invalid");
            return;
        }
        queryError.textContent = message;
        queryError.classList.remove("hidden");
        queryInput.setAttribute("aria-invalid", "true");
    }

    function setLoading(isLoading) {
        runBtn.disabled = isLoading;
        runBtnLabel.textContent = isLoading ? "Running…" : "Run Analysis";
        loader.classList.toggle("hidden", !isLoading);
        if (isLoading) {
            animatePipelineSteps();
        } else {
            clearPipelineAnimation();
        }
    }

    let pipelineTimers = [];
    function animatePipelineSteps() {
        const steps = loader.querySelectorAll(".pipeline-step");
        steps.forEach((el) => el.classList.remove("is-active"));
        const delays = [0, 4000, 9000]; // rough visual cadence; real timing varies
        steps.forEach((el, i) => {
            const t = setTimeout(() => el.classList.add("is-active"), delays[i] ?? 0);
            pipelineTimers.push(t);
        });
    }
    function clearPipelineAnimation() {
        pipelineTimers.forEach(clearTimeout);
        pipelineTimers = [];
        loader.querySelectorAll(".pipeline-step").forEach((el) => el.classList.remove("is-active"));
    }

    function setResultPane(id, text) {
        const skeleton = document.getElementById(`${id}Skeleton`);
        const pane = document.getElementById(id);
        if (skeleton) skeleton.classList.add("hidden");
        pane.classList.remove("hidden");
        pane.textContent = text && text.trim() ? text : "No data returned for this section.";
    }

    function showResultSkeletons() {
        ["research", "plan", "risk"].forEach((id) => {
            const skeleton = document.getElementById(`${id}Skeleton`);
            const pane = document.getElementById(id);
            if (skeleton) skeleton.classList.remove("hidden");
            pane.classList.add("hidden");
        });
    }

    function renderVerdict(verdict) {
        const verdictEl = document.getElementById("verdict");
        const safeVerdict = (verdict || "WATCH").toUpperCase();
        verdictEl.textContent = safeVerdict;
        verdictEl.className = "text-3xl sm:text-4xl font-bold text-center py-6 sm:py-10";

        if (safeVerdict === "BUY") {
            verdictEl.classList.add("text-green-400");
        } else if (safeVerdict === "AVOID") {
            verdictEl.classList.add("text-red-400");
        } else if (safeVerdict === "ERROR") {
            verdictEl.classList.add("text-red-400");
        } else {
            verdictEl.classList.add("text-yellow-400");
        }
    }

    // -------------------------------------------------------------------
    // Character counter + client-side validation
    // -------------------------------------------------------------------
    queryInput.addEventListener("input", () => {
        const len = queryInput.value.length;
        charCount.textContent = String(len);
        if (len > MAX_QUERY_LENGTH) {
            setFieldError(`Question is too long (${len}/${MAX_QUERY_LENGTH} characters).`);
        } else {
            setFieldError(null);
        }
    });

    // -------------------------------------------------------------------
    // Submit handler
    // -------------------------------------------------------------------
    form.addEventListener("submit", async (event) => {
        event.preventDefault();

        const query = queryInput.value.trim();

        if (!query) {
            setFieldError("Please enter a question before running the analysis.");
            queryInput.focus();
            return;
        }
        if (query.length > MAX_QUERY_LENGTH) {
            setFieldError(`Question is too long (${query.length}/${MAX_QUERY_LENGTH} characters).`);
            return;
        }
        setFieldError(null);

        // Cancel any in-flight request before starting a new one.
        if (activeController) {
            activeController.abort();
        }
        activeController = new AbortController();
        const timeoutId = setTimeout(() => activeController.abort(), REQUEST_TIMEOUT_MS);

        setLoading(true);
        setStatus("busy", "Analyzing…");
        showResultSkeletons();

        try {
            const response = await fetch("/analyze", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ query }),
                signal: activeController.signal,
            });

            let data;
            try {
                data = await response.json();
            } catch {
                throw new Error("The server returned an unreadable response.");
            }

            if (!response.ok) {
                const message = data && data.error ? data.error : `Request failed (HTTP ${response.status}).`;
                throw new Error(message);
            }

            setResultPane("research", data.research);
            setResultPane("plan", data.plan);
            setResultPane("risk", data.risk_evaluation);
            renderVerdict(data.verdict);
            setStatus("idle", "Ready");
        } catch (error) {
            if (error.name === "AbortError") {
                showToast("The request took too long and was cancelled. Please try again.");
            } else {
                showToast(error.message || "Something went wrong while running the analysis.");
            }
            setStatus("error", "Error");
            renderVerdict("ERROR");
        } finally {
            clearTimeout(timeoutId);
            setLoading(false);
        }
    });
})();
