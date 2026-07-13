{#-
  sibei-flow enrollment for dbt — an `on-run-end` hook (U1).

  Add ONE line to `dbt_project.yml` to enroll a dbt project:

      on-run-end:
        - "{{ sbflow_on_run_end(results) }}"

  On every run this macro inspects dbt's `results` and, for each failed node,
  logs a compact, greppable marker line that carries the pieces of the
  sibei-flow `Failure` contract:

      SBFLOW_FAILURE {"node_uid": "...", "status": "error", "message": "..."}

  WHY IT ONLY LOGS (does not POST): dbt hooks are Jinja/SQL evaluated against the
  warehouse — they cannot open an outbound HTTP connection, and (per ADR-0004 /
  the trust posture) sibei-flow deliberately holds no standing infra access. The
  actual POST of the `Failure` to the brain happens on the WRAPPER, which reads
  the same failure detail out of `target/run_results.json`:

      sbflow run -- dbt build          # <- this reports the failure

  So the recommended one-line dbt enrollment is simply to run dbt under the
  wrapper. This macro is the dbt-native complement: it makes failures greppable
  in the dbt log even when the run is not wrapped, and confirms the node ids
  sibei-flow will act on. When dbt runs under Airflow, the shipped
  `on_failure_callback` reports instead.
-#}
{% macro sbflow_on_run_end(results) %}
  {% if execute %}
    {% for r in results %}
      {% set status = (r.status | string) | lower %}
      {% if status in ['error', 'fail', 'runtime error', 'skipped'] and status != 'skipped' %}
        {% set message = (r.message | string) if r.message is not none else (r.node.unique_id ~ ' ' ~ status) %}
        {% set marker = tojson({'node_uid': r.node.unique_id, 'status': status, 'message': message | truncate(400, true)}) %}
        {{ log('SBFLOW_FAILURE ' ~ marker, info=True) }}
      {% endif %}
    {% endfor %}
  {% endif %}
{% endmacro %}
