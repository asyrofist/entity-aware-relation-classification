import datetime
import os
import time
import numpy as np
import tensorflow as tf
from sklearn.metrics import f1_score
import subprocess

import data_helpers
from configure import FLAGS
from model.self_att_lstm import SelfAttentiveLSTM
import utils

import warnings
import sklearn.exceptions
warnings.filterwarnings("ignore", category=sklearn.exceptions.UndefinedMetricWarning)


def train():
    with tf.device('/cpu:0'):
        train_text, train_char, train_y, train_e1, train_e2, train_dist1, train_dist2 = data_helpers.load_data_and_labels(FLAGS.train_path)
    with tf.device('/cpu:0'):
        test_text, test_char, test_y, test_e1, test_e2, test_dist1, test_dist2 = data_helpers.load_data_and_labels(FLAGS.test_path)

    char_processor = tf.contrib.learn.preprocessing.VocabularyProcessor(FLAGS.max_word_length)
    char_processor.fit([w for sent in train_char for w in sent])
    train_xchar = data_helpers.generate_char_data(train_char, char_processor)
    test_xchar = data_helpers.generate_char_data(train_char, char_processor)
    print("Char Vocabulary Size: {:d}".format(len(char_processor.vocabulary_)))
    print("train_xchar = {0}".format(train_xchar.shape))
    print("test_xchar = {0}".format(test_xchar.shape))
    print("")

    # Build vocabulary
    # Example: x_text[3] = "A misty <e1>ridge</e1> uprises from the <e2>surge</e2>."
    # ['a misty ridge uprises from the surge <UNK> <UNK> ... <UNK>']
    # =>
    # [27 39 40 41 42  1 43  0  0 ... 0]
    # dimension = MAX_SENTENCE_LENGTH
    vocab_processor = tf.contrib.learn.preprocessing.VocabularyProcessor(FLAGS.max_sentence_length)
    vocab_processor.fit(train_text)
    train_x = np.array(list(vocab_processor.transform(train_text)))
    test_x = np.array(list(vocab_processor.transform(test_text)))
    train_text = np.array(train_text)
    test_text = np.array(test_text)
    print("Text Vocabulary Size: {:d}".format(len(vocab_processor.vocabulary_)))
    print("train_x = {0}".format(train_x.shape))
    print("train_y = {0}".format(train_y.shape))
    print("test_x = {0}".format(test_x.shape))
    print("test_y = {0}".format(test_y.shape))
    print("")

    # Example: pos1[3] = [-2 -1  0  1  2   3   4 999 999 999 ... 999]
    # [95 96 97 98 99 100 101 999 999 999 ... 999]
    # =>
    # [11 12 13 14 15  16  21  17  17  17 ...  17]
    # dimension = MAX_SENTENCE_LENGTH
    dist_vocab_processor = tf.contrib.learn.preprocessing.VocabularyProcessor(FLAGS.max_sentence_length)
    dist_vocab_processor.fit(train_dist1 + train_dist2)
    train_d1 = np.array(list(dist_vocab_processor.transform(train_dist1)))
    train_d2 = np.array(list(dist_vocab_processor.transform(train_dist2)))
    test_d1 = np.array(list(dist_vocab_processor.transform(test_dist1)))
    test_d2 = np.array(list(dist_vocab_processor.transform(test_dist2)))
    print("Position Vocabulary Size: {:d}".format(len(dist_vocab_processor.vocabulary_)))

    with tf.Graph().as_default():
        session_conf = tf.ConfigProto(
            allow_soft_placement=FLAGS.allow_soft_placement,
            log_device_placement=FLAGS.log_device_placement)
        session_conf.gpu_options.allow_growth = FLAGS.gpu_allow_growth
        sess = tf.Session(config=session_conf)
        with sess.as_default():
            model = SelfAttentiveLSTM(
                sequence_length=train_x.shape[1],
                word_length=train_xchar.shape[2],
                num_classes=train_y.shape[1],
                vocab_size=len(vocab_processor.vocabulary_),
                embedding_size=FLAGS.embedding_size,
                dist_vocab_size=len(dist_vocab_processor.vocabulary_),
                dist_embedding_size=FLAGS.dist_embedding_size,
                char_vocab_size=len(char_processor.vocabulary_),
                char_embedding_size=FLAGS.char_embedding_size,
                filter_sizes=list(map(int, FLAGS.filter_sizes.split(","))),
                num_filters=FLAGS.num_filters,
                hidden_size=FLAGS.hidden_size,
                attention_size=FLAGS.attention_size,
                use_elmo=(FLAGS.embeddings == 'elmo'),
                l2_reg_lambda=FLAGS.l2_reg_lambda)

            # Define Training procedure
            global_step = tf.Variable(0, name="global_step", trainable=False)
            train_op = tf.train.AdamOptimizer(FLAGS.learning_rate).minimize(model.loss, global_step=global_step)

            # Output directory for models and summaries
            timestamp = str(int(time.time()))
            out_dir = os.path.abspath(os.path.join(os.path.curdir, "runs", timestamp))
            print("\nWriting to {}\n".format(out_dir))

            # Summaries for loss and accuracy
            loss_summary = tf.summary.scalar("loss", model.loss)
            acc_summary = tf.summary.scalar("accuracy", model.accuracy)

            # Train Summaries
            train_summary_op = tf.summary.merge([loss_summary, acc_summary])
            train_summary_dir = os.path.join(out_dir, "summaries", "train")
            train_summary_writer = tf.summary.FileWriter(train_summary_dir, sess.graph)

            # Dev summaries
            test_summary_op = tf.summary.merge([loss_summary, acc_summary])
            test_summary_dir = os.path.join(out_dir, "summaries", "dev")
            test_summary_writer = tf.summary.FileWriter(test_summary_dir, sess.graph)

            # Checkpoint directory. Tensorflow assumes this directory already exists so we need to create it
            checkpoint_dir = os.path.abspath(os.path.join(out_dir, "checkpoints"))
            checkpoint_prefix = os.path.join(checkpoint_dir, "model")
            if not os.path.exists(checkpoint_dir):
                os.makedirs(checkpoint_dir)
            saver = tf.train.Saver(tf.global_variables(), max_to_keep=FLAGS.num_checkpoints)

            # Write vocabulary
            vocab_processor.save(os.path.join(out_dir, "vocab"))
            utils.save_result(np.argmax(test_y, axis=1), os.path.join(out_dir, FLAGS.target_path), mkdir=True)

            # Initialize all variables
            sess.run(tf.global_variables_initializer())

            if FLAGS.embeddings == "word2vec":
                pretrain_W = utils.load_word2vec('resource/GoogleNews-vectors-negative300.bin', FLAGS.embedding_size, vocab_processor)
                sess.run(model.W_text.assign(pretrain_W))
                print("Success to load pre-trained word2vec model!\n")
            elif FLAGS.embeddings == "glove100":
                pretrain_W = utils.load_glove('resource/glove.6B.100d.txt', FLAGS.embedding_size, vocab_processor)
                sess.run(model.W_text.assign(pretrain_W))
                print("Success to load pre-trained glove100 model!\n")
            elif FLAGS.embeddings == "glove300":
                pretrain_W = utils.load_glove('resource/glove.840B.300d.txt', FLAGS.embedding_size, vocab_processor)
                sess.run(model.W_text.assign(pretrain_W))
                print("Success to load pre-trained glove300 model!\n")

            # Generate batches
            train_batches = data_helpers.batch_iter(list(zip(train_x, train_y, train_xchar, train_text,
                                                             train_e1, train_e2, train_d1, train_d2)),
                                              FLAGS.batch_size, FLAGS.num_epochs)
            # Training loop. For each batch...
            best_f1 = 0.0  # For save checkpoint(model)
            for train_batch in train_batches:
                train_bx, train_by, train_bchar, train_btxt, train_be1, train_be2, train_bd1, train_bd2 = zip(*train_batch)
                feed_dict = {
                    model.input_x: train_bx,
                    model.input_y: train_by,
                    model.input_char: train_bchar,
                    model.input_text: train_btxt,
                    model.input_e1: train_be1,
                    model.input_e2: train_be2,
                    model.input_d1: train_bd1,
                    model.input_d2: train_bd2,
                    model.rnn_dropout_keep_prob: FLAGS.rnn_dropout_keep_prob,
                    model.dropout_keep_prob: FLAGS.dropout_keep_prob
                }
                _, step, summaries, loss, accuracy = sess.run(
                    [train_op, global_step, train_summary_op, model.loss, model.accuracy], feed_dict)
                train_summary_writer.add_summary(summaries, step)

                # Training log display
                if step % FLAGS.display_every == 0:
                    time_str = datetime.datetime.now().isoformat()
                    print("{}: step {}, loss {:g}, acc {:g}".format(time_str, step, loss, accuracy))

                # Evaluation
                if step % FLAGS.evaluate_every == 0:
                    print("\nEvaluation:")
                    # Generate batches
                    test_batches = data_helpers.batch_iter(list(zip(test_x, test_y, test_xchar, test_text,
                                                                    test_e1, test_e2, test_d1, test_d2)),
                                                           FLAGS.batch_size, 1, shuffle=False)
                    # Training loop. For each batch...
                    losses = 0.0
                    accuracy = 0.0
                    predictions = []
                    for test_batch in test_batches:
                        test_bx, test_by, test_bchar, test_btxt, test_be1, test_be2, test_bd1, test_bd2 = zip(*test_batch)
                        feed_dict = {
                            model.input_x: test_bx,
                            model.input_y: test_by,
                            model.input_char: test_bchar,
                            model.input_text: test_btxt,
                            model.input_e1: test_be1,
                            model.input_e2: test_be2,
                            model.input_d1: test_bd1,
                            model.input_d2: test_bd2,
                            model.rnn_dropout_keep_prob: 1.0,
                            model.dropout_keep_prob: 1.0
                        }
                        summaries, loss, acc, pred = sess.run(
                            [test_summary_op, model.loss, model.accuracy, model.predictions], feed_dict)
                        test_summary_writer.add_summary(summaries, step)
                        losses += loss
                        accuracy += acc
                        predictions += pred.tolist()

                    losses /= int(len(test_y) / FLAGS.batch_size)
                    accuracy /= int(len(test_y) / FLAGS.batch_size)
                    predictions = np.array(predictions, dtype='int')
                    f1 = f1_score(np.argmax(test_y, axis=1), predictions, labels=np.array(range(1, 19)), average="macro")

                    time_str = datetime.datetime.now().isoformat()
                    print("{}: step {}, loss {:g}, acc {:g}".format(time_str, step, losses, accuracy))
                    print("(2*9+1)-Way Macro-Average F1 Score (excluding Other): {:g}\n".format(f1))

                    # Model checkpoint
                    if best_f1 * 0.98 < f1:
                        if best_f1 < f1:
                            best_f1 = f1
                        path = saver.save(sess, checkpoint_prefix+"-{:.3g}".format(f1), global_step=step)
                        output_path = FLAGS.output_path[:-4]+"-{:.3g}-{}".format(f1, step)+".txt"
                        utils.save_result(predictions, os.path.join(out_dir, output_path))
                        perl_path = os.path.join(os.path.curdir, "SemEval2010_task8_all_data",
                                                 "SemEval2010_task8_scorer-v1.2", "semeval2010_task8_scorer-v1.2.pl")
                        pfile = os.path.join(out_dir, output_path)
                        tfile = " resource/target.txt"
                        process = subprocess.Popen(["perl", perl_path, pfile, tfile], stdout=subprocess.PIPE)
                        print(str(process.communicate()[0]).split("\\n")[-2])
                        print("\nSaved model checkpoint to {}\n".format(path))


def main(_):
    train()


if __name__ == "__main__":
    tf.app.run()
