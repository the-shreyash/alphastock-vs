// =============================================================================
// StockAssist AI — MongoDB application-user bootstrap (PH2.2)
//
// WHY THIS FILE EXISTS
// --------------------
// The official mongo image creates exactly one account from
// MONGO_INITDB_ROOT_USERNAME / MONGO_INITDB_ROOT_PASSWORD: a CLUSTER ROOT user.
// The tempting shortcut is to hand that account's credentials to the
// application and move on. That is a least-privilege violation with real
// consequences: an application-level injection or a leaked container
// environment would then carry the ability to drop every database, read every
// collection, create users, and reconfigure the server.
//
// This script runs once, at first initialization, and creates a SECOND account
// scoped to `readWrite` on the application database only. The backend uses that
// one. The root account exists solely for operator tasks (backups, index
// surgery, user management) and its password never enters the backend container.
//
// HOW IT RUNS
// -----------
// The mongo image executes every *.js and *.sh in /docker-entrypoint-initdb.d
// in lexical order, using a temporary mongod bound to localhost, BEFORE the
// server starts accepting network connections. Environment variables set on the
// service are visible here as `process.env` (the image runs mongosh).
//
// ⚠  IT RUNS ONLY WHEN /data/db IS EMPTY.
//    This is a property of the upstream entrypoint, not of this file, and it is
//    the single most common source of confusion with database init scripts:
//    editing this file has NO effect on an already-initialized volume. To
//    re-run it you must destroy the data volume:
//
//        docker compose down -v          # ⚠ deletes all local database data
//
//    See docs/deployment/DOCKER_COMPOSE.md → "Troubleshooting".
// =============================================================================

(function initApplicationUser() {
    const dbName = (process.env.MONGO_INITDB_DATABASE || "").trim();
    const username = (process.env.MONGO_APP_USERNAME || "").trim();
    const password = (process.env.MONGO_APP_PASSWORD || "").trim();

    // Fail loudly rather than silently creating nothing. A missing app user is
    // not a warning — the backend would fail to authenticate on its first query
    // and the operator would be debugging an auth error against a database that
    // looks perfectly healthy. Throwing here aborts initialization, so the
    // container never reports itself ready with a half-provisioned database.
    if (!dbName || !username || !password) {
        throw new Error(
            "[init-app-user] MONGO_INITDB_DATABASE, MONGO_APP_USERNAME and " +
            "MONGO_APP_PASSWORD must all be set on the mongo service."
        );
    }

    // The application user authenticates against the application database
    // itself (authSource=<dbName>), NOT against `admin`. Keeping the credential
    // out of the admin database means a compromise of it grants nothing outside
    // this one database, and it matches the MONGO_URL that docker-compose.yml
    // builds for the backend.
    const appDb = db.getSiblingDB(dbName);

    // Idempotent by construction: the script only ever runs on an empty volume,
    // but a duplicate-user error during initialization would abort the whole
    // bootstrap, so the existing-user case is handled explicitly.
    const existing = appDb.getUser(username);
    if (existing) {
        print(`[init-app-user] user '${username}' already exists on '${dbName}' — skipping.`);
        return;
    }

    appDb.createUser({
        user: username,
        pwd: password,
        // `readWrite` — deliberately NOT `dbOwner` or `readWriteAnyDatabase`.
        //   * It covers everything the application actually does: CRUD, plus
        //     createIndex (server.py builds ~20 indexes on startup).
        //   * It does NOT cover dropDatabase, createUser, or any access to
        //     another database — including `admin`.
        // If a future feature genuinely needs more, add the narrowest role that
        // covers it rather than widening this one.
        roles: [{ role: "readWrite", db: dbName }],
    });

    print(`[init-app-user] created least-privilege user '${username}' with readWrite on '${dbName}'.`);
})();
