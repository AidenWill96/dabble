// dabble 0.5 compiled module bundle
(() => {
    "use strict";
    const __dabble = require("./dabble_server.cjs");
    if (!__dabble) throw new Error("dabble runtime not loaded");
    const __modules = Object.create(null);
    __modules["/mnt/data/dabble05/dabble/examples/fullstack/shared.dab"] = (() => {
        function site_title() {
            return "dabble 0.5 — frontend + backend";
        }
        return {"site_title": site_title};
    })();

    __modules["/mnt/data/dabble05/dabble/examples/fullstack/server.dab"] = (() => {
        const site_title = __modules["/mnt/data/dabble05/dabble/examples/fullstack/shared.dab"]["site_title"];
        function hello(req) {
            return {["message"]: site_title(), ["query"]: req.query};
        }
        __dabble.route("GET", "/api/hello", hello);
        return {"hello": hello};
    })();

    __dabble.listen({root: __dirname, port: Number(process.env.DABBLE_PORT || 8000)});
})();
