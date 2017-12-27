{% extends "layout.html.tpl" %}
{% block content %}
{% raw %}
<script id="row_tpl" type="text/template">
  <td><a href="/detail.html?merge_id={{rid}}">{{rid}}</a></td>
  <td><a href="{{gerrit_url}}/#/q/{{change_id}}">{{change_id}}</a></td>
  <td>{{feature_branch}}</td>
  <td>{{branch}}</td>
  <td>{{owner.name}}</td>
  <td>{{status}}</td>
  <td>{{request_time}}</td>
  <td>{{queue_duration}}</td>
  <td>{{merge_duration}}</td>
</script>
{% endraw %}

<script>
$(document).ready(history_page_ready);
</script>

<p>Note: click the merge id to see the details page</p>
<table id='history_table'>
<tr>
  <th>Merge #</th>
  <th>Change ID</th>
  <th>Feature Branch</th>
  <th>Target Branch</th>
  <th>Owner</th>
  <th>Result</th>
  <th>Queued At</th>
  <th>Queued For</th>
  <th>Build Duration</th>
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
