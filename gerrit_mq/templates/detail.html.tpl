{% extends "layout.html.tpl" %}
{% block content %}
<script>

var page_context = {
  active_div : 'stdout',
  follow_stream : true,
}

on_ready(function(){
  var query_obj = get_query_as_object();
  page_context.follow_stream=query_obj.follow_stream;

  console.log("Document ready, fetching data");
  show_div(null, 'stdout');
  fetch_details(query_obj.merge_id);

  var log_ctx = null;
  var stdout_ctx = null;
  var stderr_ctx = null;
});
</script>

<div>
  <h1>Merge Details</h1>
  {% include "detail_body.html.tpl" %}
</div>

<hr/>
<div>
  <p><a href="#" onclick="set_follow(event, true);">
        click to follow streaming text</a></p>
</div>

<ul class="nav nav-tabs">
  <li>
    <a href="#" onclick="show_div(event, 'log');">merge log</a>
  </li>
  <li>
    <a href="#" onclick="show_div(event, 'stdout');">stdout</a>
  </li>
  <li>
    <a href="#" onclick="show_div(event, 'stderr');">stderr</a>
  </li>
</ul>

<a name="top"></a>


<div id="log_div" class="log">
  <h2>Merge Log</h2>
  <p>&nbsp;&nbsp;&nbsp;&nbsp;
    <a id="log_dl" href="" style="display: none;">[download]</a></p>
  <pre id="log_pre"></pre>
</div>

<div id="stdout_div" class="log" style="display: none;">
  <h2>Standard Output</h2>
  <p>&nbsp;&nbsp;&nbsp;&nbsp;
    <a id="stdout_dl" href="" style="display: none;">[download]</a></p>
  <pre id="stdout_pre"></pre>
</div>

<div id="stderr_div" class="log" style="display: none;">
  <h2>Standard Error</h2>
  <p>&nbsp;&nbsp;&nbsp;&nbsp;
    <a id="stderr_dl" href="" style="display: none;">[download]</a></p>
  <pre id="stderr_pre"></pre>
</div>

<div>
<ul>
<li><a href="#" onclick="set_follow(event, false);">stop following</a></li>
<li><a href="#top" onclick="set_follow(null, false);">back to top</a></li>
</ul>
</div>
{% endblock %}
