#!/bin/bash
# VaultSync Database Identity Fix v2
# Fixes "current database cannot be renamed" by using template1 for maintenance.

set -e

DB_CONTAINER="vaultsync_db"
NEW_USER="vaultsync"
NEW_PASS="vaultsync_secure_password"
TARGET_DB="vaultsync"

echo "📡 Starting Database Identity Migration..."

# 1. Ensure the DB container is running
if ! docker ps | grep -q "$DB_CONTAINER"; then
    echo "🚀 Starting Database container..."
    docker compose up -d db
    sleep 5
fi

# 2. Identify the active superuser
echo "🔍 Identifying current database owner..."
ACTUAL_USER=""
for u in "retrosync" "neosync" "postgres" "admin"; do
    if docker exec "$DB_CONTAINER" psql -U "$u" -d template1 -c "select 1" >/dev/null 2>&1; then
        ACTUAL_USER="$u"
        break
    fi
done

if [ -z "$ACTUAL_USER" ]; then
    echo "❌ Error: Could not find an active superuser."
    exit 1
fi

echo "✅ Found active user: $ACTUAL_USER"

# 3. Create the 'vaultsync' role if it's missing
echo "🔐 Creating/Updating '$NEW_USER' role..."
docker exec "$DB_CONTAINER" psql -U "$ACTUAL_USER" -d template1 -c "DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '$NEW_USER') THEN
        CREATE ROLE $NEW_USER WITH LOGIN PASSWORD '$NEW_PASS' SUPERUSER;
    ELSE
        ALTER ROLE $NEW_USER WITH PASSWORD '$NEW_PASS' SUPERUSER;
    END IF;
END
\$\$;"

# 4. Identify and rename the database if necessary
echo "📂 Checking for old databases..."
OLD_DB=$(docker exec "$DB_CONTAINER" psql -U "$ACTUAL_USER" -d template1 -tAc "SELECT datname FROM pg_database WHERE datname IN ('retrosync', 'neosync') LIMIT 1;")

if [ ! -z "$OLD_DB" ] && [ "$OLD_DB" != "$TARGET_DB" ]; then
    echo "🔄 Found legacy database: $OLD_DB. Renaming to $TARGET_DB..."
    # Disconnect others and rename from template1 context
    docker exec "$DB_CONTAINER" psql -U "$ACTUAL_USER" -d template1 -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$OLD_DB' AND pid <> pg_backend_pid();"
    docker exec "$DB_CONTAINER" psql -U "$ACTUAL_USER" -d template1 -c "ALTER DATABASE \"$OLD_DB\" RENAME TO \"$TARGET_DB\";"
fi

# 5. Ensure the target database exists and transfer ownership
if ! docker exec "$DB_CONTAINER" psql -U "$ACTUAL_USER" -d template1 -lqt | cut -d \| -f 1 | grep -qw "$TARGET_DB"; then
    echo "🆕 Creating new database: $TARGET_DB..."
    docker exec "$DB_CONTAINER" psql -U "$ACTUAL_USER" -d template1 -c "CREATE DATABASE $TARGET_DB OWNER $NEW_USER;"
else
    echo "👑 Transferring ownership of $TARGET_DB to $NEW_USER..."
    docker exec "$DB_CONTAINER" psql -U "$ACTUAL_USER" -d template1 -c "ALTER DATABASE \"$TARGET_DB\" OWNER TO $NEW_USER;"
fi

# 6. Restart the full stack
echo "🚀 Restarting VaultSync stack..."
docker compose up --build -d

echo "✅ MIGRATION SUCCESSFUL!"
docker compose ps
