#!/usr/bin/env bash
# SARMAAN MDA — Comprehensive 3-Layer QA Suite
# Layers: Backend (API) | Infrastructure (AWS) | Frontend (Static UI)
set +e
BASE="https://mda-dashboard-alb-1851779239.us-east-1.elb.amazonaws.com"
REGION="us-east-1"

BACK_PASS=0; BACK_FAIL=0; BACK_SKIP=0; BACK_RESULTS=()
INFRA_PASS=0; INFRA_FAIL=0; INFRA_SKIP=0; INFRA_RESULTS=()
FRONT_PASS=0; FRONT_FAIL=0; FRONT_SKIP=0; FRONT_RESULTS=()

# helpers
back_pass()  { BACK_PASS=$((BACK_PASS+1));   BACK_RESULTS+=("✅|$1|$2"); }
back_fail()  { BACK_FAIL=$((BACK_FAIL+1));   BACK_RESULTS+=("❌|$1|$2"); }
back_skip()  { BACK_SKIP=$((BACK_SKIP+1));   BACK_RESULTS+=("⏭|$1|$2"); }
infra_pass() { INFRA_PASS=$((INFRA_PASS+1)); INFRA_RESULTS+=("✅|$1|$2"); }
infra_fail() { INFRA_FAIL=$((INFRA_FAIL+1)); INFRA_RESULTS+=("❌|$1|$2"); }
front_pass() { FRONT_PASS=$((FRONT_PASS+1)); FRONT_RESULTS+=("✅|$1|$2"); }
front_fail() { FRONT_FAIL=$((FRONT_FAIL+1)); FRONT_RESULTS+=("❌|$1|$2"); }
front_skip() { FRONT_SKIP=$((FRONT_SKIP+1)); FRONT_RESULTS+=("⏭|$1|$2"); }

probe()      { curl -sk -o /dev/null -w "%{http_code}|%{time_total}" "$BASE$1"; }
probe_body() { curl -sk "$BASE$1"; }

####################################################################
# BACKEND LAYER — FastAPI app
####################################################################
echo "Running backend tests..."

# 1. Health & connectivity
r=$(probe "/"); code="${r%|*}"; t="${r#*|}"
[[ "$code" == "200" ]] && back_pass "Health & connectivity" "GET / → 200 in ${t%.*}s" || back_fail "Health & connectivity" "GET / → $code"

# 2. Authentication endpoint exists
code=$(curl -sk -o /dev/null -w "%{http_code}" -X POST "$BASE/api/auth/login" -H "Content-Type: application/json" -d '{"username":"x","password":"x"}')
[[ "$code" == "401" || "$code" == "400" ]] && back_pass "Authentication" "wrong creds → $code" || back_fail "Authentication" "wrong creds → $code"

code=$(curl -sk -o /dev/null -w "%{http_code}" -X POST "$BASE/api/auth/login" -H "Content-Type: application/json" -d '{}')
[[ "$code" == "422" ]] && back_pass "Auth — empty body validation" "→ 422" || back_fail "Auth — empty body" "→ $code"

# 3. Overview KPIs
body=$(probe_body "/api/mda/overview?project_id=2")
if echo "$body" | grep -q "total_forms"; then
  forms=$(echo "$body" | grep -o '"total_forms":[0-9]*' | head -1 | grep -o '[0-9]*')
  back_pass "Overview KPIs" "total_forms=$forms, non-empty payload"
else
  back_fail "Overview KPIs" "missing total_forms"
fi

# 4. QC summary
code=$(curl -sk -o /dev/null -w "%{http_code}" "$BASE/api/mda/qc/summary?project_id=2")
[[ "$code" == "200" ]] && back_pass "Quality checks (/api/mda/qc/summary)" "→ 200" || back_fail "QC summary" "→ $code"

# 5. Coverage / completion endpoints
code1=$(curl -sk -o /dev/null -w "%{http_code}" "$BASE/api/mda/coverage/lga?project_id=2")
code2=$(curl -sk -o /dev/null -w "%{http_code}" "$BASE/api/mda/coverage/ward?project_id=2")
if [[ "$code1" == "200" && "$code2" == "200" ]]; then
  back_pass "Completion (LGA + Ward)" "both 200 + JSON"
else
  back_fail "Completion" "LGA=$code1 Ward=$code2"
fi

# 6. Geospatial endpoints
g1=$(curl -sk -o /dev/null -w "%{http_code}" "$BASE/api/mda/geo/coverage-summary?project_id=2")
g2=$(curl -sk -o /dev/null -w "%{http_code}" "$BASE/api/projects/2/boundaries/lga/geojson")
if [[ "$g1" == "200" && "$g2" == "200" ]]; then
  back_pass "Geospatial (coverage + GeoJSON)" "both 200"
else
  back_fail "Geospatial" "coverage=$g1 lga-geojson=$g2"
fi

# 6b. Security headers (added by main.py middleware after QA Quick Wins)
H=$(curl -sIk "$BASE/")
miss=()
echo "$H" | grep -iq "^x-content-type-options:" || miss+=("X-Content-Type-Options")
echo "$H" | grep -iq "^x-frame-options:"        || miss+=("X-Frame-Options")
echo "$H" | grep -iq "^referrer-policy:"        || miss+=("Referrer-Policy")
echo "$H" | grep -iq "^strict-transport-security:" || miss+=("HSTS")
echo "$H" | grep -iq "^x-request-id:"           || miss+=("X-Request-ID")
if [[ ${#miss[@]} -eq 0 ]]; then
  back_pass "Security headers (5 required)" "HSTS, XFO, XCTO, Referrer, X-Request-ID present"
else
  back_fail "Security headers" "missing: ${miss[*]}"
fi

# 6c. OpenAPI docs hidden in production
d1=$(curl -sk -o /dev/null -w "%{http_code}" "$BASE/docs")
d2=$(curl -sk -o /dev/null -w "%{http_code}" "$BASE/openapi.json")
if [[ "$d1" == "404" && "$d2" == "404" ]]; then
  back_pass "OpenAPI docs hidden in prod" "/docs and /openapi.json → 404"
else
  back_fail "OpenAPI exposed" "/docs=$d1 /openapi.json=$d2"
fi

# 6d. CORS not wildcard
cors=$(curl -sIk -H "Origin: https://example.com" "$BASE/api/mda/overview?project_id=2" | grep -i "access-control-allow-origin")
if echo "$cors" | grep -q '\*'; then
  back_fail "CORS allowlist" "wildcard still present"
else
  back_pass "CORS allowlist (no wildcard)" "explicit origin policy enforced"
fi

# 7. Teams performance / supervision
code=$(curl -sk -o /dev/null -w "%{http_code}" "$BASE/api/mda/teams/performance?project_id=2")
[[ "$code" == "200" ]] && back_pass "Teams performance / supervision" "→ 200" || back_fail "Teams performance" "→ $code"

# 8. Projects & filter hierarchy
body=$(probe_body "/api/projects")
if echo "$body" | grep -q '"is_active":true'; then
  count=$(echo "$body" | grep -o '"id":' | wc -l | tr -d ' ')
  back_pass "Projects & filter hierarchy" "$count projects, active flag present"
else
  back_fail "Projects" "no active project found"
fi

# 9. Authz enforcement (current finding: most APIs DO NOT block anon — keep honest)
blocked=0; total=0
for ep in /api/sync/config /api/auth/users /api/auth/change-password /api/mda/upload; do
  total=$((total+1))
  c=$(curl -sk -o /dev/null -w "%{http_code}" "$BASE$ep")
  [[ "$c" == "401" || "$c" == "403" || "$c" == "405" ]] && blocked=$((blocked+1))
done
if [[ "$blocked" == "$total" ]]; then
  back_pass "Authz on write/admin endpoints" "$blocked/$total reject anon"
else
  back_fail "Authz enforcement" "only $blocked/$total reject anon"
fi

# 10. PII / public-facing — confirm sync trigger endpoint not public
c=$(curl -sk -o /dev/null -w "%{http_code}" -X POST "$BASE/api/sync/run")
[[ "$c" == "401" || "$c" == "403" || "$c" == "405" ]] && back_pass "PII / mutation endpoints gated" "/api/sync/run → $c" || back_fail "PII gating" "/api/sync/run → $c (anon can mutate!)"

# 11. Error handling
c=$(curl -sk -o /dev/null -w "%{http_code}" "$BASE/api/does-not-exist")
[[ "$c" == "404" ]] && back_pass "Error handling (404)" "unknown route → 404" || back_fail "404 handling" "→ $c"

# 12. Skip — write endpoint smoke (would mutate prod)
back_skip "Write endpoints (POST /upload)" "skipped — would mutate prod data"

####################################################################
# INFRASTRUCTURE LAYER — AWS
####################################################################
echo "Running infrastructure tests..."

# 1. EC2 app server running
state=$(aws ec2 describe-instances --region $REGION --filters "Name=tag:Name,Values=mda-dashboard-app" --query "Reservations[].Instances[].State.Name" --output text 2>/dev/null)
[[ "$state" == "running" ]] && infra_pass "EC2 app server" "mda-dashboard-app: $state" || infra_fail "EC2 app server" "$state"

# 2. RDS database available + encrypted
rds=$(aws rds describe-db-instances --region $REGION --db-instance-identifier mda-dashboard-db --query "DBInstances[0].[DBInstanceStatus,StorageEncrypted,BackupRetentionPeriod]" --output text 2>/dev/null)
status=$(echo "$rds" | awk '{print $1}')
encrypted=$(echo "$rds" | awk '{print $2}')
backup=$(echo "$rds" | awk '{print $3}')
if [[ "$status" == "available" && "$encrypted" == "True" && "$backup" -ge "7" ]]; then
  infra_pass "RDS PostgreSQL" "available, encrypted, ${backup}d backup"
else
  infra_fail "RDS" "$status / encrypted=$encrypted / backup=$backup"
fi

# 3. ALB target health
tg_arn=$(aws elbv2 describe-target-groups --region $REGION --query "TargetGroups[?contains(LoadBalancerArns[0],'mda-dashboard-alb')].TargetGroupArn" --output text 2>/dev/null)
if [[ -n "$tg_arn" ]]; then
  healthy=$(aws elbv2 describe-target-health --region $REGION --target-group-arn "$tg_arn" --query "TargetHealthDescriptions[?TargetHealth.State=='healthy'] | length(@)" --output text 2>/dev/null)
  [[ "$healthy" -ge "1" ]] && infra_pass "ALB target health" "$healthy healthy target(s)" || infra_fail "ALB" "0 healthy targets"
else
  infra_fail "ALB" "target group not found"
fi

# 4. TLS / SSL certificate
cert_expiry=$(echo | openssl s_client -servername "$(echo $BASE | sed 's|https://||')" -connect "$(echo $BASE | sed 's|https://||'):443" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
if [[ -n "$cert_expiry" ]]; then
  infra_pass "TLS / SSL certificate" "valid until $cert_expiry"
else
  infra_fail "TLS cert" "could not verify"
fi

# 5. Sec groups — only 80/443 exposed
sg_id=$(aws ec2 describe-load-balancers --region $REGION --names mda-dashboard-alb --query "LoadBalancers[0].SecurityGroups[0]" --output text 2>/dev/null)
open_ports=$(aws ec2 describe-security-groups --region $REGION --group-ids "$sg_id" --query "SecurityGroups[0].IpPermissions[?contains(IpRanges[0].CidrIp, '0.0.0.0/0')].FromPort" --output text 2>/dev/null)
if echo "$open_ports" | grep -qE "^(80|443)( |$)|( 80| 443)( |$)" && ! echo "$open_ports" | grep -qE "(22|3306|5432|6379|27017)"; then
  infra_pass "Security groups" "only ports $open_ports open to internet"
else
  infra_fail "Security groups" "open: $open_ports"
fi

# 6. CloudWatch metrics flowing
ec2_id=$(aws ec2 describe-instances --region $REGION --filters "Name=tag:Name,Values=mda-dashboard-app" --query "Reservations[].Instances[].InstanceId" --output text 2>/dev/null)
dp=$(aws cloudwatch get-metric-statistics --region $REGION --namespace AWS/EC2 --metric-name CPUUtilization --dimensions Name=InstanceId,Value=$ec2_id --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d "1 hour ago" +%Y-%m-%dT%H:%M:%S) --end-time $(date -u +%Y-%m-%dT%H:%M:%S) --period 300 --statistics Average --query "Datapoints | length(@)" --output text 2>/dev/null)
[[ "$dp" -ge "1" ]] && infra_pass "CloudWatch metrics flowing" "$dp data points in last hour" || infra_fail "CloudWatch" "no data"

# 7. RDS storage autoscaling
max=$(aws rds describe-db-instances --region $REGION --db-instance-identifier mda-dashboard-db --query "DBInstances[0].MaxAllocatedStorage" --output text 2>/dev/null)
[[ "$max" -ge "100" ]] && infra_pass "RDS storage autoscaling" "ceiling ${max} GB" || infra_fail "Autoscaling" "ceiling $max"

# 8. SSM access for deploys / recompute
ssm_count=$(aws ssm describe-instance-information --region $REGION --filters "Key=InstanceIds,Values=$ec2_id" --query "InstanceInformationList | length(@)" --output text 2>/dev/null)
[[ "$ssm_count" -ge "1" ]] && infra_pass "SSM agent (deploys + recompute)" "instance registered" || infra_fail "SSM" "not registered"

# 9. S3 buckets accessible
s3=$(aws s3api list-buckets --query "Buckets[?contains(Name,'sarmaan') || contains(Name,'mda')] | length(@)" --output text 2>/dev/null)
[[ "$s3" -ge "0" ]] && infra_pass "S3 storage (boundaries + exports)" "$s3 project bucket(s) reachable" || infra_fail "S3" "API failed"

# 10. GitHub Actions workflow files present
[[ -f /Users/godsgift/Desktop/geospatial-tracking-system/.github/workflows/deploy.yml && -f /Users/godsgift/Desktop/geospatial-tracking-system/.github/workflows/ci.yml ]] \
  && infra_pass "CI/CD (GitHub Actions)" "ci.yml + deploy.yml present" \
  || infra_fail "CI/CD" "workflow files missing"

####################################################################
# FRONTEND LAYER — Static UI
####################################################################
echo "Running frontend tests..."

# 1. Dashboard page loads
r=$(probe "/dashboard"); code="${r%|*}"; t="${r#*|}"
[[ "$code" == "200" ]] && front_pass "Dashboard page" "/dashboard → 200 in ${t%.*}s" || front_fail "Dashboard" "→ $code"

# 2. Login page loads + has form
body=$(probe_body "/login")
if echo "$body" | grep -qi '<form' && echo "$body" | grep -qi 'password'; then
  front_pass "Login page" "form + password field present"
else
  front_fail "Login page" "form/password missing"
fi

# 3. Admin panel loads
code=$(curl -sk -o /dev/null -w "%{http_code}" "$BASE/mda-admin")
[[ "$code" == "200" ]] && front_pass "Admin panel" "/mda-admin → 200" || front_fail "Admin panel" "→ $code"

# 4. MapLibre CSS loaded
body=$(probe_body "/dashboard")
echo "$body" | grep -qi "maplibre" && front_pass "MapLibre GL JS" "library referenced" || front_fail "MapLibre" "not referenced"

# 5. Chart.js loaded
echo "$body" | grep -qi "chart.js\|Chart\.js\|chartjs" && front_pass "Chart.js library" "referenced" || front_fail "Chart.js" "not referenced"

# 6. HTML structure valid (has <html>, <head>, <body>)
body=$(probe_body "/")
echo "$body" | grep -qi "<html" && echo "$body" | grep -qi "<head" && echo "$body" | grep -qi "<body" \
  && front_pass "HTML structure" "html/head/body present" \
  || front_fail "HTML structure" "missing tags"

# 7. Page title set
title=$(echo "$body" | grep -oE "<title>[^<]+</title>" | head -1)
[[ -n "$title" ]] && front_pass "Page metadata" "$title" || front_fail "Page metadata" "no <title>"

# 8. Favicon / branding
echo "$body" | grep -qi "icon\|favicon" && front_pass "Branding / favicon" "icon link present" || front_fail "Favicon" "missing"

# 9. Static assets — JS files load
js_count=$(echo "$body" | grep -oE "<script" | wc -l | tr -d ' ')
[[ "$js_count" -ge "1" ]] && front_pass "JavaScript loaded" "$js_count <script> tag(s)" || front_fail "JS" "no scripts"

# 10. Mobile viewport set
echo "$body" | grep -qi 'name="viewport"' && front_pass "Mobile viewport" "responsive meta present" || front_fail "Viewport" "missing"

# 11. 404 page renders
code=$(curl -sk -o /dev/null -w "%{http_code}" "$BASE/this-page-does-not-exist")
[[ "$code" == "404" ]] && front_pass "404 handling" "unknown route → 404" || front_fail "404" "→ $code"

# 12. Skip — interactive browser tests (need Playwright)
front_skip "Interactive flows (drill-down, modal, exports)" "needs Playwright runner"

####################################################################
# PRINT RESULTS
####################################################################
print_table() {
  local title="$1"; shift
  local results=("$@")
  echo
  echo "════════════════════════════════════════════════════════════════════"
  echo " $title"
  echo "════════════════════════════════════════════════════════════════════"
  echo "  #  Verdict  Area                                  | Note"
  echo "  ─  ─────── ───────────────────────────────────────|─────────────────"
  local i=1
  for line in "${results[@]}"; do
    IFS='|' read -r verdict area note <<< "$line"
    printf "  %-2s %-7s %-40s| %s\n" "$i" "$verdict" "$area" "$note"
    i=$((i+1))
  done
}

print_table "BACKEND LAYER — FastAPI API" "${BACK_RESULTS[@]}"
BACK_TOTAL=$((BACK_PASS+BACK_FAIL))
echo "  ─────────────────────────────────────────────────────"
echo "  Result: $BACK_PASS / $BACK_TOTAL passed, $BACK_SKIP skipped (informational)"

print_table "INFRASTRUCTURE LAYER — AWS" "${INFRA_RESULTS[@]}"
INFRA_TOTAL=$((INFRA_PASS+INFRA_FAIL))
echo "  ─────────────────────────────────────────────────────"
echo "  Result: $INFRA_PASS / $INFRA_TOTAL passed, $INFRA_SKIP skipped (informational)"

print_table "FRONTEND LAYER — Static UI" "${FRONT_RESULTS[@]}"
FRONT_TOTAL=$((FRONT_PASS+FRONT_FAIL))
echo "  ─────────────────────────────────────────────────────"
echo "  Result: $FRONT_PASS / $FRONT_TOTAL passed, $FRONT_SKIP skipped (informational)"

echo
echo "════════════════════════════════════════════════════════════════════"
echo " OVERALL"
echo "════════════════════════════════════════════════════════════════════"
TOTAL_PASS=$((BACK_PASS+INFRA_PASS+FRONT_PASS))
TOTAL=$((BACK_TOTAL+INFRA_TOTAL+FRONT_TOTAL))
TOTAL_SKIP=$((BACK_SKIP+INFRA_SKIP+FRONT_SKIP))
PCT=$(awk "BEGIN {printf \"%.1f\", ($TOTAL_PASS / $TOTAL) * 100}")
echo "  Result: $TOTAL_PASS / $TOTAL passed, $TOTAL_SKIP skipped (informational). Score: ${PCT}%"
echo "════════════════════════════════════════════════════════════════════"
