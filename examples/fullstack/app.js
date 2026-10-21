// dabble 0.5 compiled module bundle
(() => {
    "use strict";
    const __dabble = window.dabble;
    if (!__dabble) throw new Error("dabble runtime not loaded");
    const __modules = Object.create(null);
    __modules["/mnt/data/dabble05/dabble/examples/fullstack/shared.dab"] = (() => {
        function site_title() {
            return "dabble 0.5 — frontend + backend";
        }
        return {"site_title": site_title};
    })();

    __modules["/mnt/data/dabble05/dabble/examples/fullstack/app.dab"] = (() => {
        const site_title = __modules["/mnt/data/dabble05/dabble/examples/fullstack/shared.dab"]["site_title"];
        function App() {
            let count, message;
            count = __dabble.signal(0);
            message = __dabble.signal("Click Load API to fetch from the dabble backend.");
            function increment() {
                count.set((count.get() + 1));
            }
            async function load() {
                let data;
                data = (await __dabble.fetch_json("/api/hello"));
                message.set(data["message"]);
            }
            return __dabble.scope(__dabble.main(__dabble.h1(site_title()), __dabble.p("Counter: ", count), __dabble.button("Add 1", {"onclick": increment}), __dabble.button("Load API", {"onclick": load}), __dabble.p(message)), "dabble-App");
        }
        __dabble.mount("#app", App);
        return {"App": App};
    })();

})();
