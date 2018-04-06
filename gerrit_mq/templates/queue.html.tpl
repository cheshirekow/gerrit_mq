{% extends "layout.html.tpl" %}
{% block content %}
{% raw %}
<script id="row_tpl" type="text/template">
  <td>{{queue_index}}</td>
  <td>{{subject}}</td>
  <td>{{feature_branch}}</td>
  <td>{{branch}}</td>
  <td>{{owner.name}}</td>
  <td> <a href="{{gerrit_url}}/#/q/{{change_id}}">{{change_id}}</a></td>
</script>
{% endraw %}

<script>
on_ready(queue_page_ready);
</script>

<div id="current_merge_div" style="display: none;">
  <h1>Active Merge</h1>
  {% include "detail_body.html.tpl" %}
</div>


<h1>Current Queue</h1>
<p><b>Note:</b> click the merge id to see the details page</p>
<p><b>Note:</b>
   The queue shown here is representative of the change queue at the time it
   was last polled from gerrit, so there may be a delay before the most recently
   queued changes are shown here. In addition, the state of the queue is not
   updated from gerrit during a merge.</p>
<table>
  <thead>
    <tr>
      <tr>
      <th>Queue</th>
      <th>Subject</th>
      <th>Feature Branch</th>
      <th>Target Branch</th>
      <th>Owner</th>
      <th>Change ID</th>
    </tr>
  </thead>
  <tbody id='history_table'>
  </tbody>
</table>

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
