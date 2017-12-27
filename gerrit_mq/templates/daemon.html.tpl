{% extends "layout.html.tpl" %}
{% block content %}


<script>
$(document).ready(daemon_page_ready);
</script>

<div id="table_div">
  <h1>Daemon status</h1>
  <table>
    <tr>
      <td>Alive</td>
      <td id="alive_cell">??</td>
    </tr>
    <tr>
      <td>Paused</td>
      <td id="paused_cell">??</td>
      <td><a href="" id="pause_button"
             onclick="pause_daemon(event, true);">[pause]</a></td>
    </tr>
    <tr>
      <td>pid</td>
      <td id="pid_cell">??</td>
    </tr>
  </table>
</div>
{% endblock %}
