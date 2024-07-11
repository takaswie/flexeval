/*
This is a configuration for evaluting the quality of responses generated by an AI assistant.
Originally used to generate scores for the Japanese versions of MT-bench or Vicuna-bench.

Translated and adapted from [lm-sys/FastChat](https://github.com/lm-sys/FastChat/blob/main/fastchat/llm_judge/data/judge_prompts.jsonl).
*/
{
  class_path: 'ChatLLMScore',
  init_args: {
    language_model: { class_path: 'OpenAIChatAPI', init_args: { model: 'gpt-4-turbo-2024-04-09' } },
    valid_score_range: [1, 10],
    prompt_template: {
      class_path: 'Jinja2PromptTemplate',
      init_args: {
        template: |||
          [指示]
          {% if references|length > 0 -%}
          以下に表示されるユーザの質問に対するアシスタントの応答の品質を評価してください。評価は正確さと有用性を考慮すべきです。アシスタントの回答の言語は、ユーザが使用している言語と一致しているべきで、そうでない場合は減点されるべきです。参照回答とアシスタントの回答が与えられます。あなたの評価は、アシスタントの回答と参照回答を比較することから始めてください。ミスを特定し、訂正してください。できるだけ客観的であること。評価の説明をした後、"[[rating]]"という形式で、1から10までの整数の評価値を出力してください（例 "rating：[[5]]"）。
          {%- else -%}
          以下に表示されるユーザの質問に対するアシスタントの応答の品質を公平に評価してください。評価は、応答の有用性、関連性、正確性、深さ、創造性、詳細度などの要素を考慮すべきです。アシスタントの回答の言語は、ユーザが使用している言語と一致しているべきで、そうでない場合は減点されるべきです。評価は短い説明から始めてください。できるだけ客観的であること。評価の説明をした後、"[[rating]]"という形式で、1から10までの整数の評価値を出力してください（例 "rating：[[5]]"）。
          {%- endif %}

          [ユーザの質問]
          {{ messages[0]["content"] }}

          {% if references|length > 0 -%}
          [参考回答の開始]
          {{ references[0] }}
          [参考回答の終了]
          {% endif -%}
          [アシスタントの回答開始]
          {% if messages|length == 1 %}{{ lm_output }}{% else %}{{ messages[1]["content"] }}{% endif %}
          [アシスタントの回答終了]
        |||,
      },
    },
    system_message: "あなたは優秀な助手です。",
  },
}
