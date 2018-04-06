{% extends "layout.html.tpl" %}
{% block content %}

<script>
on_ready(history_page_ready);
</script>

<div id="current_merge_div" style="display: none;">
  <h1>Active Merge</h1>
  {% include "detail_body.html.tpl" %}
</div>


<h1>Merge History</h1>
<p>Note: click the merge id to see the details page</p>
<table>
<tr>
  <thead>
    <th>Merge #</th>
    <th>Target Branch</th>
    <th>Change ID</th>
    <th>Feature Branch</th>
    <th>Owner</th>
    <th>Queued At</th>
    <th>Queued For</th>
    <th>Result</th>
    <th>Build Duration</th>
  </thead>
  <tbody id='history_table'>
  </tbody>
</tr>

</table>
<ul class="pager">
  <li><a id="first_page_anchor" href="?page=0">first</a></li>
  <li><a id="prev_page_anchor" href="?page=0">prev</a></li>
  <li>
    <form action="" class="form-inline">
      <input class="form-control" id="page_input" size="6" type="text"
             value="0" name="page"/>
      <input class="form-control" id="page_size_input" type="hidden"
             value="25" name="page_size" />
      <input class="form-control" type="submit" name="submit" value="goto"/>
    </form>
  </li>
  <li><a id="next_page_anchor" href="?page=0">next</a></li>
  <li><a id="last_page_anchor" href="?page=0">last</a></li>
</ul>
{% endblock %}
