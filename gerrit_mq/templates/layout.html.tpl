<html>
  <head>
    <title>Gerrit Merge Serializer</title>
    <link rel="stylesheet"
          href="https://maxcdn.bootstrapcdn.com/bootstrap/3.3.6/css/bootstrap.min.css"
          integrity="sha384-1q8mTJOASx8j1Au+a5WDVnPi2lkFfwwEAa8hDDdjZlpLegxhjVME1fgjWPGmkzs7"
          crossorigin="anonymous" />
    <link rel="stylesheet" href="style.css" />

    <script src="https://cdnjs.cloudflare.com/ajax/libs/mustache.js/2.3.0/mustache.min.js" >
      </script>
    <script src="script.js"></script>
  </head>
  <body>

{% raw %}
<script id="details_tpl" type="text/template">
  <tr>
    <td>Merge #</td>
    <td><a href="detail.html?merge_id={{merge.rid}}">{{merge.rid}}</a></td>
  </tr>
  <tr>
    <td>Target Branch</td>
    <td>{{merge.branch}}</td>
  </tr>
  <tr class="{{result_class}}">
    <td>Result</td>
    <td>{{status_text}} [{{merge.status}}]</td>
    <td><a href="" onclick="cancel_merge(event, {{merge.rid}});"
           style="{{cancel_style}}">[cancel]</a></td>
  </tr>
  <tr>
    <td>Started Build At</td>
    <td>{{merge.start_time}}</td>
  </tr>
  <tr>
    <td>Finished Build At</td>
    <td>{{merge.end_time}}</td>
  </tr>
</script>

<script id="change_tpl" type="text/template">
  <td><a href="{{gerrit_url}}/#/q/{{change.change_id}}">{{change.change_id}}</a></td>
  <td>{{feature_branch}}</td>
  <td>{{change.owner.name}}</td>
  <td>{{change.request_time}}</td>
  <td>{{queue_duration}}</td>
</script>

<script id="history_head_tpl" type="text/template">
  <td rowspan="{{rowspan}}">
    <a href="/detail.html?merge_id={{merge.rid}}">{{merge.rid}}</a></td>
  <td rowspan="{{rowspan}}">{{merge.branch}}</td>

  <td><a href="{{gerrit_url}}/#/q/{{change.change_id}}">
        {{change.change_id}}</a></td>
  <td>{{feature_branch}}</td>
  <td>{{change.owner.name}}</td>
  <td>{{change.request_time}}</td>
  <td>{{durations.queue}}</td>

  <td rowspan="{{rowspan}}">{{status_text}}: {{merge.status}}</td>
  <td rowspan="{{rowspan}}">{{durations.merge}}</td>
</script>

<script id="history_tail_tpl" type="text/template">
  <td><a href="{{gerrit_url}}/#/q/{{change.change_id}}">
        {{change.change_id}}</a></td>
  <td>{{feature_branch}}</td>
  <td>{{change.owner.name}}</td>
  <td>{{change.request_time}}</td>
  <td>{{durations.queue}}</td>
</script>


{% endraw %}

<div class="navigation">
  <ul>
    <li> <a href="daemon.html">daemon status</a> </li>
    <li> <a href="history.html">merge history</a> </li>
    <li> <a href="queue.html">current queue</a> </li>
  </ul>
</div>
<div class="content">
{% block content -%}
{%- endblock %}
</div>

  </body>
</html>
